"""
Tests for lib/services/app_initializer.py — AppInitializer

Covers:
- _load_configuration() — returns correct config via g_data
- get_provider_config() — returns correct dict for known/unknown providers
- _cleanup_on_failure() — properly closes resources in reverse order
- initialize() failure at each step — memory_db, tools, services, adapters
- cleanup() — proper shutdown ordering
"""

import asyncio
import io
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Mock heavy optional dependencies to avoid import cascade failures.
# app_initializer.py has module-level imports from many service modules.
# We only stub them while this test module is executing.
# ---------------------------------------------------------------------------
_STUB_MODS = [
    'neo4j', 'neo4j.exceptions', 'neo4j.graph', 'neo4j.time',
    'discord', 'discord.ext', 'discord.ext.commands',
    'sentence_transformers',
    'colorama',
    # Service modules that pull in heavy transitive deps
    'lib.memory_db',
    'lib.services.tool_context',
    'lib.system_prompt_parser',
    'lib.ai_providers',
    'lib.services.memory_service',
    'lib.services.context_builder',
    'lib.services.model_cache',
    'lib.services.memory_extractor',
    'lib.services.adapter_manager',
    'lib.services.vision_service',
    'lib.services.sandbox_manager',
    'lib.services.memory_analytics',
    'lib.services.memory_consolidation',
    'lib.services.task_scheduler',
    'lib.services.heartbeat_service',
    'lib.services.heartbeat_modules',
    'lib.services.event_bus',
]


@pytest.fixture(autouse=True, scope="module")
def _stub_heavy_modules():
    """Stub out heavy imports so AppInitializer is importable."""
    saved = {mod: sys.modules.get(mod) for mod in _STUB_MODS}
    for mod in _STUB_MODS:
        if mod == 'lib.services.event_bus':
            continue  # keep the real event_bus — needed for EventBus/Event type checks
        sys.modules[mod] = MagicMock()
    yield
    for mod, orig in saved.items():
        if mod == 'lib.services.event_bus':
            continue  # keep real event_bus
        if orig is None:
            sys.modules.pop(mod, None)
        else:
            sys.modules[mod] = orig

from lib.global_registry import GlobalRegistry, g_data
from lib.configuration_file import ConfigurationFile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeClosable:
    """Mock object that tracks whether close() was called."""
    def __init__(self, name="unknown"):
        self.name = name
        self.closed = False
        self.stop_called = False

    async def close(self):
        self.closed = True

    async def stop(self):
        self.stop_called = True

    async def stop_all(self):
        self.stop_called = True

    async def disconnect_all(self):
        self.closed = True

    async def stop_periodic_consolidation(self):
        self.stop_called = True


class FakeClosableThatRaises:
    """Mock object whose close/stop methods raise."""
    def __init__(self, name="bad"):
        self.name = name

    async def close(self):
        raise RuntimeError(f"{self.name}.close() failed")

    async def stop(self):
        raise RuntimeError(f"{self.name}.stop() failed")

    async def stop_all(self):
        raise RuntimeError(f"{self.name}.stop_all() failed")

    async def disconnect_all(self):
        raise RuntimeError(f"{self.name}.disconnect_all() failed")

    async def stop_periodic_consolidation(self):
        raise RuntimeError(f"{self.name}.stop_periodic_consolidation() failed")


def _fresh_registry():
    """Reset singleton's registry to a clean state."""
    # The g_data singleton persists across tests, so we clear its state.
    g_data._registry.clear()
    return g_data


def _make_cfg(**overrides):
    """Create a minimal config dict and set it as g_data['cfg']."""
    data = {
        'neo4j': {
            'uri': 'bolt://localhost:7687',
            'user': 'neo4j',
            'pass': 'test',
        },
        'memory': {
            'embedding_model': 'all-MiniLM-L6-v2',
        },
        'providers': {
            'ollama': {'url': 'http://localhost:11434'},
            'openai': {'api_key': 'sk-test'},
        },
        'default_provider': 'ollama',
        'default_model': 'llama3.2',
        'default_system_prompt': 'nami',
        'paths': {'system_prompt_dir': 'system_prompt'},
        'bot': {'timezone': 'UTC'},
        'vision': {},
        'heartbeat': {},
        'sandbox': {'enabled': False},
        'dream': {'enabled': False},
    }
    data.update(overrides)
    cfg = ConfigurationFile('test-config.yml', data)
    g_data._registry['cfg'] = cfg
    return cfg


def _fresh_app(config_path="test-config.yml"):
    """Create a fresh AppInitializer instance with a fresh registry."""
    _fresh_registry()
    _make_cfg()

    from lib.services.app_initializer import AppInitializer
    return AppInitializer(config_path)


# ---------------------------------------------------------------------------
# _load_configuration
# ---------------------------------------------------------------------------

def test_load_configuration_returns_config():
    """_load_configuration() loads config via g_data.get_or_create."""
    app = _fresh_app()
    cfg = app._load_configuration()
    if cfg is None:
        assert False, f"returned None"
    if not isinstance(cfg, ConfigurationFile):
        assert False, f"not a ConfigurationFile: {type(cfg)}"
    assert cfg.data.get('default_provider') == 'ollama', f"wrong default_provider: {cfg.data.get('default_provider')}"


def test_load_configuration_returns_cached():
    """_load_configuration() returns same config on second call."""
    app = _fresh_app()
    cfg1 = app._load_configuration()
    cfg2 = app._load_configuration()
    assert cfg1 is cfg2


# ---------------------------------------------------------------------------
# get_provider_config
# ---------------------------------------------------------------------------

def test_get_provider_config_known():
    """get_provider_config returns config dict for known provider."""
    app = _fresh_app()
    app.config = app._load_configuration()
    result = app.get_provider_config("ollama")
    assert result == {'url': 'http://localhost:11434'}, f"got {result}"


def test_get_provider_config_openai():
    """get_provider_config returns config for openai with api_key."""
    app = _fresh_app()
    app.config = app._load_configuration()
    result = app.get_provider_config("openai")
    assert result == {'api_key': 'sk-test'}, f"got {result}"


def test_get_provider_config_unknown():
    """get_provider_config returns empty dict for unknown provider."""
    app = _fresh_app()
    app.config = app._load_configuration()
    result = app.get_provider_config("nonexistent")
    assert result == {}, f"expected {{}}, got {result}"


def test_get_provider_config_no_providers_section():
    """get_provider_config returns empty dict when no providers configured."""
    app = _fresh_app()
    _make_cfg()  # overwrite with empty providers
    app.config = g_data.get("cfg")
    app.config.data.pop('providers', None)
    result = app.get_provider_config("ollama")
    assert result == {}, f"expected {{}}, got {result}"


# ---------------------------------------------------------------------------
# _cleanup_on_failure
# ---------------------------------------------------------------------------

def test_cleanup_on_failure_stops_adapters_then_closes_memory_db():
    """_cleanup_on_failure stops adapters then closes memory_db."""
    app = _fresh_app()
    app.config = app._load_configuration()

    adapter_mgr = FakeClosable("adapter_manager")
    mem_db = FakeClosable("memory_db")
    g_data._registry['adapter_manager'] = adapter_mgr
    g_data._registry['memory_db'] = mem_db

    call_order = []
    orig_stop = adapter_mgr.stop_all
    orig_close = mem_db.close

    async def track_stop():
        call_order.append('adapter_stop')
        await orig_stop()
    async def track_close():
        call_order.append('memory_close')
        await orig_close()

    adapter_mgr.stop_all = track_stop
    mem_db.close = track_close

    async def run():
        await app._cleanup_on_failure()
    asyncio.run(run())

    if call_order != ['adapter_stop', 'memory_close']:
        assert False, f"wrong order: {call_order}"
    if not adapter_mgr.stop_called:
        assert False, f"adapter_manager.stop_all() not called"
    assert mem_db.closed


def test_cleanup_on_failure_handles_missing_resources():
    """_cleanup_on_failure does not raise when resources missing."""
    app = _fresh_app()
    app.config = app._load_configuration()
    # Neither adapter_manager nor memory_db in g_data

    async def run():
        await app._cleanup_on_failure()
    asyncio.run(run())  # must not raise


def test_cleanup_on_failure_adapter_stop_raises():
    """_cleanup_on_failure continues past adapter stop failure."""
    app = _fresh_app()
    app.config = app._load_configuration()

    bad_adapter = FakeClosableThatRaises("adapter_manager")
    mem_db = FakeClosable("memory_db")
    g_data._registry['adapter_manager'] = bad_adapter
    g_data._registry['memory_db'] = mem_db

    async def run():
        await app._cleanup_on_failure()
    asyncio.run(run())  # must not raise

    assert mem_db.closed


def test_cleanup_on_failure_memory_db_close_raises():
    """_cleanup_on_failure handles memory_db close failure."""
    app = _fresh_app()
    app.config = app._load_configuration()

    adapter_mgr = FakeClosable("adapter_manager")
    bad_db = FakeClosableThatRaises("memory_db")
    g_data._registry['adapter_manager'] = adapter_mgr
    g_data._registry['memory_db'] = bad_db

    async def run():
        await app._cleanup_on_failure()
    asyncio.run(run())  # must not raise

    assert adapter_mgr.stop_called


# ---------------------------------------------------------------------------
# initialize() failure at each step
# ---------------------------------------------------------------------------

def test_initialize_fails_at_memory_db():
    """initialize() raises RuntimeError when memory_db init fails."""
    app = _fresh_app()
    app.config = app._load_configuration()

    async def failing_init_mem_db():
        raise RuntimeError("Neo4j connection refused")

    app._initialize_memory_db = failing_init_mem_db
    app._cleanup_on_failure = AsyncMock()

    async def run():
        try:
            await app.initialize()
            return False
        except RuntimeError as e:
            if "Initialization failed at 'memory_db'" not in str(e):
                assert False, f"wrong error: {e}"
            return True

    result = asyncio.run(run())
    if not result:
        assert False, f"did not raise RuntimeError"
    if not app._cleanup_on_failure.called:
        assert False, f"_cleanup_on_failure not called"
    assert app._init_step == 'memory_db', f"_init_step={app._init_step}"


def test_initialize_fails_at_tools():
    """initialize() raises RuntimeError when tools init fails."""
    app = _fresh_app()
    app.config = app._load_configuration()

    app._initialize_memory_db = AsyncMock()
    app._initialize_tools = AsyncMock(side_effect=RuntimeError("MCP connection failed"))
    app._cleanup_on_failure = AsyncMock()

    async def run():
        try:
            await app.initialize()
            return False
        except RuntimeError as e:
            if "Initialization failed at 'tools'" not in str(e):
                assert False, f"wrong error: {e}"
            return True

    result = asyncio.run(run())
    if not result:
        assert False, f"did not raise RuntimeError"
    if not app._cleanup_on_failure.called:
        assert False, f"_cleanup_on_failure not called"
    assert app._init_step == 'tools', f"_init_step={app._init_step}"


def test_initialize_fails_at_services():
    """initialize() raises RuntimeError when services init fails."""
    app = _fresh_app()
    app.config = app._load_configuration()

    app._initialize_memory_db = AsyncMock()
    app._initialize_tools = AsyncMock()
    app._initialize_services = AsyncMock(side_effect=RuntimeError("EventBus init failed"))
    app._cleanup_on_failure = AsyncMock()

    async def run():
        try:
            await app.initialize()
            return False
        except RuntimeError as e:
            if "Initialization failed at 'services'" not in str(e):
                assert False, f"wrong error: {e}"
            return True

    result = asyncio.run(run())
    if not result:
        assert False, f"did not raise RuntimeError"
    if not app._cleanup_on_failure.called:
        assert False, f"_cleanup_on_failure not called"
    assert app._init_step == 'services', f"_init_step={app._init_step}"


def test_initialize_fails_at_adapters():
    """initialize() raises RuntimeError when adapters init fails."""
    app = _fresh_app()
    app.config = app._load_configuration()

    app._initialize_memory_db = AsyncMock()
    app._initialize_tools = AsyncMock()
    app._initialize_services = AsyncMock()
    app._initialize_adapters = AsyncMock(side_effect=RuntimeError("Adapter init failed"))
    app._cleanup_on_failure = AsyncMock()

    async def run():
        try:
            await app.initialize()
            return False
        except RuntimeError as e:
            if "Initialization failed at 'adapters'" not in str(e):
                assert False, f"wrong error: {e}"
            return True

    result = asyncio.run(run())
    if not result:
        assert False, f"did not raise RuntimeError"
    if not app._cleanup_on_failure.called:
        assert False, f"_cleanup_on_failure not called"
    assert app._init_step == 'adapters', f"_init_step={app._init_step}"


def test_initialize_sets_init_step_to_complete():
    """initialize() sets _init_step to 'complete' on success."""
    app = _fresh_app()
    app.config = app._load_configuration()

    app._initialize_memory_db = AsyncMock()
    app._initialize_tools = AsyncMock()
    app._initialize_services = AsyncMock()
    app._initialize_adapters = AsyncMock()

    async def run():
        await app.initialize()

    asyncio.run(run())
    assert app._init_step == 'complete', f"_init_step={app._init_step}, expected 'complete"


# ---------------------------------------------------------------------------
# cleanup() — proper shutdown ordering
# ---------------------------------------------------------------------------

def test_cleanup_order():
    """cleanup() shuts down resources in the correct order."""
    app = _fresh_app()
    app.config = app._load_configuration()

    call_order = []

    event_bus = FakeClosable("event_bus")
    mcp_client = FakeClosable("mcp_client")
    consolidation_svc = FakeClosable("consolidation_service")
    heartbeat_svc = FakeClosable("heartbeat_service")
    scheduler = FakeClosable("task_scheduler")
    adapter_mgr = FakeClosable("adapter_manager")
    mem_db = FakeClosable("memory_db")

    g_data._registry['event_bus'] = event_bus
    g_data._registry['mcp_client'] = mcp_client
    g_data._registry['consolidation_service'] = consolidation_svc
    g_data._registry['heartbeat_service'] = heartbeat_svc
    g_data._registry['task_scheduler'] = scheduler
    g_data._registry['adapter_manager'] = adapter_mgr
    g_data._registry['memory_db'] = mem_db

    # Wrap methods to track call order
    async def track_publish(event):
        call_order.append('publish_shutdown')
    event_bus.publish = track_publish

    async def track_disconnect():
        call_order.append('mcp_disconnect')
        await mcp_client.disconnect_all.__wrapped__ if hasattr(mcp_client.disconnect_all, '__wrapped__') else None
    mcp_client.disconnect_all = track_disconnect

    async def track_stop_consolidation():
        call_order.append('stop_consolidation')
    consolidation_svc.stop_periodic_consolidation = track_stop_consolidation

    async def track_stop_heartbeat():
        call_order.append('stop_heartbeat')
    heartbeat_svc.stop = track_stop_heartbeat

    async def track_stop_scheduler():
        call_order.append('stop_scheduler')
    scheduler.stop = track_stop_scheduler

    async def track_stop_adapters():
        call_order.append('stop_adapters')
    adapter_mgr.stop_all = track_stop_adapters

    async def track_close_db():
        call_order.append('close_memory_db')
    mem_db.close = track_close_db

    async def run():
        await app.cleanup()
    asyncio.run(run())

    expected = [
        'publish_shutdown',
        'mcp_disconnect',
        'stop_consolidation',
        'stop_heartbeat',
        'stop_scheduler',
        'stop_adapters',
        'close_memory_db',
    ]
    assert call_order == expected, f"expected {expected}, got {call_order}"


def test_cleanup_handles_missing_resources():
    """cleanup() does not raise when resources are missing."""
    app = _fresh_app()
    app.config = app._load_configuration()
    # No resources in g_data

    async def run():
        await app.cleanup()
    asyncio.run(run())  # must not raise


def test_cleanup_handles_failing_mcp_disconnect():
    """cleanup() continues past MCP disconnect failure."""
    app = _fresh_app()
    app.config = app._load_configuration()

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    bad_mcp = FakeClosableThatRaises("mcp_client")
    mem_db = FakeClosable("memory_db")

    g_data._registry['event_bus'] = event_bus
    g_data._registry['mcp_client'] = bad_mcp
    g_data._registry['memory_db'] = mem_db

    async def run():
        await app.cleanup()
    asyncio.run(run())  # must not raise

    assert mem_db.closed


def test_cleanup_skips_none_values():
    """cleanup() correctly skips g_data entries that are None."""
    app = _fresh_app()
    app.config = app._load_configuration()

    event_bus = FakeClosable("event_bus")
    mem_db = FakeClosable("memory_db")

    g_data._registry['event_bus'] = event_bus
    g_data._registry['mcp_client'] = None
    g_data._registry['consolidation_service'] = None
    g_data._registry['heartbeat_service'] = None
    g_data._registry['task_scheduler'] = None
    g_data._registry['adapter_manager'] = None
    g_data._registry['memory_db'] = mem_db

    event_bus.publish = AsyncMock()

    async def run():
        await app.cleanup()
    asyncio.run(run())  # must not raise

    if not mem_db.closed:
        assert False, f"memory_db.close() should be called"
    assert event_bus.publish.called


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
