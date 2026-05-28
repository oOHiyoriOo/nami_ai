"""
Application initializer - handles startup configuration.
Single responsibility: Initialize application components.
"""
import logging
from colorama import Fore

from lib.memory_db import MemoryDb
from lib.global_registry import g_data
from lib.configuration_file import ConfigurationFile
from lib.services.tool_context import ToolContext
from lib.system_prompt_parser import NamiSystemPrompt
from lib.ai_providers import ProviderRegistry
from lib.services.memory_service import MemoryService
from lib.services.context_builder import ContextBuilder
from lib.services.model_cache import ModelCache
from lib.services.memory_extractor import MemoryExtractor
from lib.services.adapter_manager import AdapterManager
from lib.services.adapter_ws_server import AdapterWebSocketServer
from lib.services.vision_service import VisionService
from lib.services.sandbox_manager import SandboxManager, get_sandbox_password, get_sandbox_ssh_key
from lib.services.memory_analytics import MemoryAnalytics
from lib.services.memory_consolidation import MemoryConsolidationService
from lib.services.task_scheduler import TaskScheduler
from lib.services.event_bus import EventBus, Event
from lib.services.heartbeat_service import HeartbeatService
from lib.services.heartbeat_modules import SystemHealthCheck, MemoryGrooming, DreamModule, CuriosityModule
from lib.services.tool_response_log import ToolResponseLog
from lib.services.message_state_cache import MessageStateCache


class AppInitializer:
    """Handles application initialization."""

    def __init__(self, config_path: str = "config.yml"):
        """
        Initialize with config path.

        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path
        self.config = None
        self._init_step = "not started"

    async def initialize(self):
        """
        Initialize all application components.
        
        Raises:
            RuntimeError: If initialization fails, with details about which step failed.
        """
        logging.info("Initializing Personality Proxy API...")

        try:
            # Load configuration
            self._init_step = "configuration"
            self.config = self._load_configuration()

            # Initialize components
            self._init_step = "memory_db"
            await self._initialize_memory_db()
            
            self._init_step = "tools"
            await self._initialize_tools()
            
            self._init_step = "services"
            await self._initialize_services()
            
            self._init_step = "adapters"
            await self._initialize_adapters()

            self._init_step = "complete"
            self._log_startup_info()

        except Exception as e:
            logging.error(f"Initialization failed at step '{self._init_step}': {e}")
            await self._cleanup_on_failure()
            raise RuntimeError(f"Initialization failed at '{self._init_step}': {e}") from e

    async def _cleanup_on_failure(self):
        """Clean up any partially initialised resources."""
        logging.info(f"Cleaning up after failed initialization (failed at: {self._init_step})")

        try:
            adapter_manager = g_data.get("adapter_manager")
            if adapter_manager:
                await adapter_manager.stop_all()
        except Exception as e:
            logging.warning(f"Error stopping adapter_manager during cleanup: {e}")

        try:
            memory_db = g_data.get("memory_db")
            if memory_db:
                await memory_db.close()
        except Exception as e:
            logging.warning(f"Error closing memory_db during cleanup: {e}")

    def _load_configuration(self) -> ConfigurationFile:
        """Load configuration file."""
        cfg = g_data.get_or_create("cfg", ConfigurationFile.load, self.config_path)
        return cfg

    def get_provider_config(self, provider_name: str) -> dict:
        """
        Get configuration for a specific provider.

        Args:
            provider_name: Name of the provider

        Returns:
            Provider configuration dict
        """
        return self.config.data.get('providers', {}).get(provider_name, {})

    async def _initialize_memory_db(self):
        """Initialize Neo4j memory database and ensure schema indices exist.

        If the configured embedding model has a different dimension than the
        existing vector index, stale embeddings are cleared automatically and
        a background task regenerates them via Ollama.
        """
        memory_settings = self.config.data.get('memory', {})
        embedding_model = memory_settings.get('embedding_model', 'nomic-embed-text')
        embedding_dimension = int(memory_settings.get('embedding_dimension', 768))
        embedding_max_input_chars = int(memory_settings.get('embedding_max_input_chars', 6000))
        ollama_url = self.config.data.get('providers', {}).get('ollama', {}).get('url', 'http://localhost:11434')

        memory_db_instance = g_data.get_or_create(
            "memory_db",
            MemoryDb,
            neo4j_uri=self.config.data['neo4j']['uri'],
            neo4j_user=self.config.data['neo4j']['user'],
            neo4j_pass=self.config.data['neo4j']['pass'],
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            embedding_max_input_chars=embedding_max_input_chars,
            ollama_url=ollama_url,
        )
        await memory_db_instance.setup_indices()
        logging.info(f"Memory database initialized with model: {embedding_model} (dim={embedding_dimension})")

        # Regenerate any embeddings cleared by a model migration (runs in background)
        import asyncio
        asyncio.ensure_future(memory_db_instance.regenerate_missing_embeddings())

    async def _initialize_tools(self):
        """Load tools from both local modules and MCP servers via ToolContext."""
        ctx = await ToolContext.for_chat()
        g_data.get_or_create("tools", lambda: ctx.tools)
        g_data.get_or_create("tool_context", lambda: ctx)

        local_count = len([t for t in ctx.tools if "categories" in t])
        mcp_count = len(ctx.tools) - local_count
        logging.info(f"Loaded {local_count} local tools and {mcp_count} MCP tools (total: {len(ctx.tools)})")

    async def _initialize_services(self):
        """Initialize application services."""
        # --- EventBus (created first — services subscribe during init) ---
        event_bus = EventBus()
        g_data.get_or_create("event_bus", lambda: event_bus)

        # --- Tool response log (SQLite storage for bulky tool responses) ---
        paths = self.config.data.get("paths", {})
        tool_log_db = paths.get("tool_response_db", "tool_responses.db")
        tool_response_log = ToolResponseLog(db_path=tool_log_db)
        await tool_response_log.initialize()
        retention_days = self.config.data.get("bot", {}).get(
            "tool_response_retention_days", 30
        )
        await tool_response_log.prune_old(retention_days=retention_days)
        g_data.get_or_create("tool_response_log", lambda: tool_response_log)

        # Get memory settings from config and store in registry
        memory_settings = self.config.data.get('memory', {})
        g_data.get_or_create("memory_settings", lambda: memory_settings)
        
        similarity_threshold = memory_settings.get('similarity_threshold', 0.65)
        
        # Create memory service
        memory_db = g_data.get("memory_db")
        memory_service = MemoryService(
            memory_db, 
            similarity_threshold=similarity_threshold
        )
        g_data.get_or_create("memory_service", lambda: memory_service)

        # Create vision service
        vision_config = self.config.data.get('vision', {})
        vision_service = VisionService(vision_config)
        g_data.get_or_create("vision_service", lambda: vision_service)

        # Load default system prompt (can be overridden per provider)
        paths = self.config.data.get('paths', {})
        prompt_dir = paths.get('system_prompt_dir', 'system_prompt')
        default_prompt = self.config.data.get('default_system_prompt', 'nami')
        prompt_path = f"{prompt_dir}/{default_prompt}.md"
        tz_name = self.config.data.get('bot', {}).get('timezone', 'Europe/Berlin')
        sys_prompt_instance = g_data.get_or_create(
            "system_prompt",
            lambda: NamiSystemPrompt.load(prompt_path, tz_name)
        )

        # Create context builder
        memory_window_turns = self.config.data.get("memory", {}).get("window_turns", 3)
        context_builder = ContextBuilder(sys_prompt_instance, memory_service, memory_window_turns)
        g_data.get_or_create("context_builder", lambda: context_builder)

        # Create model cache
        model_cache = ModelCache()
        g_data.get_or_create("model_cache", lambda: model_cache)

        # Create memory extractor (AI-powered memory extraction)
        memory_config = self.config.data.get('memory', {})
        memory_extractor = MemoryExtractor(
            provider_registry=ProviderRegistry,
            memory_db=memory_db,
            provider_name=memory_config.get('extraction_provider', 'ollama'),
            model_name=memory_config.get('extraction_model')
        )
        g_data.get_or_create("memory_extractor", lambda: memory_extractor)

        # Create memory analytics (monitoring and diagnostics)
        memory_analytics_svc = MemoryAnalytics(memory_db, memory_service.hierarchy)
        g_data.get_or_create("memory_analytics", lambda: memory_analytics_svc)

        # Create memory consolidation service (periodic deduplication and merging)
        consolidation_svc = MemoryConsolidationService(
            memory_db=memory_db,
            decay_service=memory_service.decay_service
        )
        g_data.get_or_create("consolidation_service", lambda: consolidation_svc)
        await consolidation_svc.start_periodic_consolidation()

        # Initialize sandbox manager (SSH-based isolated execution environment)
        sandbox_config = self.config.data.get('sandbox', {})
        if sandbox_config.get('enabled', False):
            password = get_sandbox_password(sandbox_config.get('password'))
            if not password:
                logging.warning(
                    "Sandbox enabled but no password found. Set SANDBOX_PASSWORD env var, "
                    "ensure /secrets/sandbox_password exists, or set sandbox.password in config.yml"
                )
            else:
                ssh_key = get_sandbox_ssh_key()
                sandbox = SandboxManager(
                    host=sandbox_config.get('host', 'sandbox'),
                    port=sandbox_config.get('port', 22),
                    username=sandbox_config.get('username', 'root'),
                    password=password,
                    ssh_key_path=ssh_key,
                    fg_timeout=sandbox_config.get('fg_timeout', 15.0),
                    max_output_kb=sandbox_config.get('max_output_kb', 16),
                )
                g_data.get_or_create("sandbox_manager", lambda: sandbox)
                logging.info(f"Sandbox manager initialized (host={sandbox.host}, auth={'key' if ssh_key else 'password'})")

        # Initialize task scheduler (AI self-scheduling)
        paths = self.config.data.get('paths', {})
        scheduler_db = paths.get('scheduler_db', 'scheduler.db')
        scheduler = TaskScheduler(db_path=scheduler_db, event_bus=event_bus)
        g_data.get_or_create("task_scheduler", lambda: scheduler)
        await scheduler.start()

        # Initialize HeartbeatService (autonomous tick loop with pluggable modules)
        hb_cfg = self.config.data.get("heartbeat", {})
        heartbeat = HeartbeatService(config=self.config, db_path=scheduler_db)

        # Register modules — each implements condition() + action()
        heartbeat.register(SystemHealthCheck())
        heartbeat.register(MemoryGrooming(config=self.config, db_path=scheduler_db))

        # DreamModule replaces the old DreamService poll loop
        dream_cfg = self.config.data.get("dream", {})
        dream_module = None
        if dream_cfg.get("enabled", True):
            dream_module = DreamModule(config=self.config, db_path=scheduler_db)
            heartbeat.register(dream_module)
            g_data.get_or_create("dream_service", lambda: dream_module)
        else:
            logging.info("[dream] Auto-Dream disabled in config")

        await heartbeat.start()
        g_data.get_or_create("heartbeat_service", lambda: heartbeat)

        # Register CuriosityModule — Nami's autonomous learning engine
        curiosity_module = None
        curiosity_cfg = self.config.data.get("heartbeat", {}).get("modules", {}).get("curiosity", {})
        if curiosity_cfg.get("enabled", True):
            curiosity_module = CuriosityModule(config=self.config, db_path=scheduler_db)
            heartbeat.register(curiosity_module)
            g_data.get_or_create("curiosity_module", lambda: curiosity_module)
        else:
            logging.info("[curiosity] CuriosityModule disabled in config")

        # Register custom bio+mood module (pluggable, lives in custom/)
        try:
            from custom import register_all
            register_all(heartbeat, g_data, self.config)
            logging.info("[custom] Bio+Mood module registered")
        except ImportError:
            logging.debug("[custom] Bio+Mood module not available (custom/ not found)")
        except Exception as e:
            logging.warning("[custom] Bio+Mood module failed to load: %s", e)

        # Initialize NotificationPipeline (proactive message delivery)
        from lib.services.notification_pipeline import NotificationPipeline
        notification_pipeline = NotificationPipeline(
            config=self.config, event_bus=event_bus
        )
        g_data.get_or_create("notification_pipeline", lambda: notification_pipeline)

        # Wire TaskNotificationQueue — buffers task.completed events for context injection
        from lib.services.task_notification_queue import TaskNotificationQueue
        task_notification_queue = TaskNotificationQueue()
        g_data.get_or_create("task_notification_queue", lambda: task_notification_queue)
        event_bus.subscribe("task.completed", task_notification_queue.on_task_completed)

        # Wire HeartbeatService as subscriber for events
        event_bus.subscribe("system.startup_complete", heartbeat._on_startup_complete)
        event_bus.subscribe("memory.extracted", heartbeat._on_memory_extracted)

        # Wire activity.recorded — resets idle timers for dream, curiosity, heartbeat
        event_bus.subscribe("activity.recorded", lambda e: heartbeat.record_event())
        if dream_module is not None:
            event_bus.subscribe("activity.recorded", lambda e: dream_module.record_activity())
        if curiosity_module is not None:
            event_bus.subscribe("activity.recorded", lambda e: curiosity_module.record_activity())

        # Wire message.send — routes proactive outbound messages to adapters
        # NOTE: adapter_manager is created in _initialize_adapters() (after this method),
        # so subscription is deferred there.

        logging.info("Services initialized")

        # Publish startup event — subscribers react after everything is wired
        await event_bus.publish(Event(type="system.startup_complete", data={}))

    def _log_startup_info(self):
        """Log startup information."""
        available_providers = list(self.config.data.get('providers', {}).keys())
        default_prompt = self.config.data.get('default_system_prompt', 'nami')

        logging.info("=" * 70)
        logging.info(f"{Fore.GREEN}Nami AI initialised — waiting for adapter connections")
        logging.info(f"Available providers: {', '.join(available_providers)}")
        logging.info(f"Default model: {self.config.data.get('default_provider', 'ollama')}/{self.config.data.get('default_model', '(not set)')}")
        logging.info(f"Default personality: {default_prompt}")
        logging.info("Model format: <provider>/<model> (e.g., ollama/llama3.2, copilot/gpt-4.1)")
        logging.info("Adapters connect via WebSocket at /api/ws/adapter")
        logging.info("=" * 70)
    
    async def _initialize_adapters(self):
        """Initialize WebSocket adapter server and AI pipeline handler."""
        from lib.services.event_bus import EventBus
        from lib.services.ai_pipeline_handler import AIPipelineHandler

        event_bus = g_data.get_or_create("event_bus", EventBus)

        # Initialize WebSocket adapter server and register in g_data
        ws_server = AdapterWebSocketServer(self.config.data)
        g_data.get_or_create("adapter_ws_server", lambda: ws_server)

        # Message state cache — SQLite-backed, survives restarts
        paths = self.config.data.get("paths", {})
        scheduler_db = paths.get("scheduler_db", "scheduler.db")
        msg_cache = MessageStateCache(db_path=scheduler_db)
        await msg_cache.init()
        msg_cache.start()
        ws_server.set_message_state_cache(msg_cache)
        g_data.get_or_create("message_state_cache", lambda: msg_cache)

        # Re-queue any messages that were mid-flight when the server last restarted
        await msg_cache.requeue_lost(event_bus)

        # Wire response.ready → ws_server so AI responses reach adapters
        ws_server.subscribe_to_event_bus(event_bus)

        # AdapterManager is a thin wrapper over ws_server for proactive sends
        adapter_manager = AdapterManager(ws_server)
        g_data.get_or_create("adapter_manager", lambda: adapter_manager)

        # Wire message.send → adapter_manager so tools can send messages via event bus
        event_bus.subscribe("message.send", adapter_manager.on_message_send)

        # AIPipelineHandler subscribes to message.received + task.due
        AIPipelineHandler(event_bus)

    async def cleanup(self):
        """Cleanup resources on shutdown."""
        logging.info("Shutting down Personality Proxy API...")

        # Publish shutdown event before tearing down services
        event_bus = g_data.get("event_bus")
        if event_bus:
            await event_bus.publish(Event(type="system.shutdown", data={}))
        
        # Disconnect MCP servers
        mcp_client = g_data.get("mcp_client")
        if mcp_client:
            try:
                await mcp_client.disconnect_all()
                logging.info("MCP servers disconnected")
            except Exception as e:
                logging.error(f"Error disconnecting MCP servers: {e}")
        
        # Stop consolidation service
        consolidation_svc = g_data.get("consolidation_service")
        if consolidation_svc:
            await consolidation_svc.stop_periodic_consolidation()

        # Stop heartbeat service (before scheduler — they share scheduler.db)
        heartbeat_service = g_data.get("heartbeat_service")
        if heartbeat_service:
            await heartbeat_service.stop()

        # Stop task scheduler
        scheduler = g_data.get("task_scheduler")
        if scheduler:
            await scheduler.stop()

        # Stop message state cache cleanup task
        msg_cache = g_data.get("message_state_cache")
        if msg_cache:
            await msg_cache.stop()

        # Stop adapter manager
        adapter_manager = g_data.get("adapter_manager")
        if adapter_manager:
            try:
                await adapter_manager.stop_all()
            except Exception as e:
                logging.error(f"Error stopping adapter_manager: {e}")

        memory_db = g_data.get("memory_db")
        if memory_db:
            await memory_db.close()

        logging.info("Shutdown complete")
