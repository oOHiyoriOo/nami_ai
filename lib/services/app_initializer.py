"""
Application initializer - handles startup configuration.
Single responsibility: Initialize application components.
"""
import logging
import json
from colorama import Fore

from lib.memory_db import MemoryDb
from lib.tool_loader import load_tools
from lib.global_registry import g_data
from lib.asyncsqlite import AsyncSQLite
from lib.configurationFile import ConfigurationFile
from lib.system_prompt_parser import NamiSystemPrompt
from lib.ai_providers import ProviderRegistry
from lib.services.memory_service import MemoryService
from lib.services.context_builder import ContextBuilder


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

    async def initialize(self):
        """Initialize all application components."""
        logging.info("Initializing Personality Proxy API...")

        # Load configuration
        self.config = self._load_configuration()

        # Initialize components
        await self._initialize_memory_db()
        await self._initialize_history_db()
        await self._initialize_tools()
        await self._initialize_services()

        self._log_startup_info()

    def _load_configuration(self) -> ConfigurationFile:
        """Load configuration file."""
        cfg = g_data.get_or_create("cfg", ConfigurationFile, self.config_path)
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
        """Initialize Neo4j memory database."""
        memory_db_instance = g_data.get_or_create(
            "memory_db",
            MemoryDb,
            neo4j_uri=self.config.data['neo4j']['uri'],
            neo4j_user=self.config.data['neo4j']['user'],
            neo4j_pass=self.config.data['neo4j']['pass'],
            model_name=self.config.data.get('memory_db', {}).get('model', 'all-MiniLM-L6-v2')
        )
        logging.info("Memory database initialized")

    async def _initialize_history_db(self):
        """Initialize SQLite history database."""
        with open('lib/Storage/history_schem.json') as schema_file:
            history_db = g_data.get_or_create(
                "history_db",
                AsyncSQLite,
                db_path="history.db",
                schema=json.load(schema_file)
            )

        await history_db.initialize()
        logging.info("History database initialized")

    async def _initialize_tools(self):
        """Load tools."""
        loaded_tools = await load_tools(None)
        g_data.get_or_create("tools", lambda: loaded_tools)
        logging.info(f"Loaded {len(loaded_tools)} tools")

    async def _initialize_services(self):
        """Initialize application services."""
        # Create memory service
        memory_db = g_data.get("memory_db")
        memory_service = MemoryService(memory_db)
        g_data.get_or_create("memory_service", lambda: memory_service)

        # Load default system prompt (can be overridden per provider)
        default_prompt = self.config.data.get('default_system_prompt', 'nami')
        sys_prompt_instance = g_data.get_or_create(
            "system_prompt",
            NamiSystemPrompt,
            f"system_prompt/{default_prompt}.md"
        )

        # Create context builder
        context_builder = ContextBuilder(sys_prompt_instance, memory_service)
        g_data.get_or_create("context_builder", lambda: context_builder)

        logging.info("Services initialized")

    def _log_startup_info(self):
        """Log startup information."""
        available_providers = list(self.config.data.get('providers', {}).keys())
        default_prompt = self.config.data.get('default_system_prompt', 'nami')

        logging.info("=" * 70)
        logging.info(f"{Fore.GREEN}Personality Proxy API initialized!")
        logging.info(f"Available providers: {', '.join(available_providers)}")
        logging.info(f"Default personality: {default_prompt}")
        logging.info(f"Model format: <provider>/<model> (e.g., ollama/llama2, copilot/gpt-4.1)")
        logging.info("=" * 70)

    async def cleanup(self):
        """Cleanup resources on shutdown."""
        logging.info("Shutting down Personality Proxy API...")

        memory_db = g_data.get("memory_db")
        if memory_db:
            memory_db.close()

        logging.info("Shutdown complete")
