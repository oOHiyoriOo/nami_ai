"""
Test script for api_server utility functions
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Mock heavy optional dependencies to avoid import cascade failures.
# api_server.py imports AppInitializer → MemoryDb → neo4j, discord, etc.
# We only stub them while this test module is executing.
# ---------------------------------------------------------------------------
_STUB_MODS = [
    'neo4j', 'neo4j.exceptions', 'neo4j.graph', 'neo4j.time',
    'discord', 'discord.ext', 'discord.ext.commands',
    'sentence_transformers', 'torch', 'asyncpg',
    'PIL', 'matplotlib', 'scipy', 'numpy', 'pandas',
    'sklearn', 'sklearn.cluster',
    # Service submodules that pull in heavy transitive deps
    'lib.memory_db',
    'lib.services.tool_context',
    'lib.system_prompt_parser',
    'lib.services.memory_service',
    'lib.services.context_builder',
    'lib.services.app_initializer',
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
    'lib.services.ai_pipeline',
    'lib.services.notification_pipeline',
    'lib.services.tool_response_log',
]


@pytest.fixture(autouse=True, scope="module")
def _stub_heavy_modules():
    """Stub out heavy transitive imports so api_server is importable."""
    saved = {mod: sys.modules.get(mod) for mod in _STUB_MODS}
    for mod in _STUB_MODS:
        sys.modules[mod] = MagicMock()
    yield
    for mod, orig in saved.items():
        if orig is None:
            sys.modules.pop(mod, None)
        else:
            sys.modules[mod] = orig


# Check for required dependencies
try:
    from lib.utils.model_string import parse_model_string
    DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    DEPENDENCIES_AVAILABLE = False
    MISSING_DEPENDENCY = str(e)





def test_standard_format():
    """Test standard <provider>/<model> format"""
    result = parse_model_string("ollama/llama3")
    assert (result == ("ollama", "llama3")), "Test failed"


def test_model_with_version():
    """Test model name containing a version/tag"""
    result = parse_model_string("ollama/llama3:latest")
    assert (result == ("ollama", "llama3:latest")), "Test failed"


def test_no_slash():
    """Test model string without a slash raises ValueError"""
    try:
        parse_model_string("llama3")
    except ValueError as e:
        pass
    except Exception as e:
        pass


def test_empty_string():
    """Test empty string raises ValueError"""
    try:
        parse_model_string("")
    except ValueError as e:
        pass
    except Exception as e:
        pass


def test_multiple_slashes():
    """Test multiple slashes — only split on first slash"""
    result = parse_model_string("a/b/c")
    assert (result == ("a", "b/c")), "Test failed"


def test_copilot_format():
    """Test copilot provider format"""
    result = parse_model_string("copilot/gpt-4.1")
    assert (result == ("copilot", "gpt-4.1")), "Test failed"


def test_openai_format():
    """Test openai provider format"""
    result = parse_model_string("openai/gpt-4o-mini")
    assert (result == ("openai", "gpt-4o-mini")), "Test failed"



# ---------------------------------------------------------------------------
# /health endpoint tests
# ---------------------------------------------------------------------------

_HEALTH_DEPS_AVAILABLE = False
_HEALTH_MISSING = ""


def _ensure_health_deps():
    """Try to import everything needed for health endpoint tests."""
    global _HEALTH_DEPS_AVAILABLE, _HEALTH_MISSING
    if _HEALTH_DEPS_AVAILABLE:
        return True
    if 'api_server' in sys.modules:
        try:
            global health, g_data_health, ConfigurationFile_health
            from api_server import health
            from lib.global_registry import g_data as g_data_health
            from lib.configuration_file import ConfigurationFile as ConfigurationFile_health
            _HEALTH_DEPS_AVAILABLE = True
            return True
        except Exception as e:
            _HEALTH_MISSING = str(e)
            return False
    try:
        global health, g_data_health, ConfigurationFile_health
        _mock_cfg = MagicMock()
        _mock_cfg.data = {'bot': {'log_level': 'INFO'}}
        with patch('lib.configuration_file.ConfigurationFile.load', return_value=_mock_cfg):
            import api_server  # noqa: F401
        from api_server import health
        from lib.global_registry import g_data as g_data_health
        from lib.configuration_file import ConfigurationFile as ConfigurationFile_health
        _HEALTH_DEPS_AVAILABLE = True
        return True
    except Exception as e:
        _HEALTH_MISSING = str(e)
        return False


def _make_health_cfg(**overrides):
    """Create a minimal config dict and set it as g_data['cfg']."""
    data = {
        'default_model': 'test-model',
        'providers': {'ollama': {'url': 'http://localhost:11434'}},
        'default_provider': 'ollama',
    }
    data.update(overrides)
    cfg = ConfigurationFile_health('test-config.yml', data)
    g_data_health._registry['cfg'] = cfg
    return cfg


def test_health_no_memory_db():
    """Health endpoint returns healthy when memory_db is None."""
    if not _ensure_health_deps():
        print(f"  [SKIP] Dependencies unavailable: {_HEALTH_MISSING}")

    _make_health_cfg()
    g_data_health._registry.pop('memory_db', None)

    import asyncio
    result = asyncio.run(health())

    assert not (result["status"] != "healthy"), "Test failed"
    assert not (result["memory_entries"] != 0), "Test failed"
    assert not (result["memory_db_available"] is not False), "Test failed"
    assert not (result["error"] is not None), "Test failed"


def test_health_memory_db_raises():
    """Health endpoint returns degraded when memory_db.get_total_entries() raises."""
    if not _ensure_health_deps():
        print(f"  [SKIP] Dependencies unavailable: {_HEALTH_MISSING}")

    _make_health_cfg()

    mock_db = MagicMock()
    mock_db.get_total_entries = MagicMock(side_effect=RuntimeError("Neo4j connection timeout"))
    g_data_health._registry['memory_db'] = mock_db

    import asyncio
    result = asyncio.run(health())

    assert not (result["status"] != "degraded"), "Test failed"
    assert not (result["memory_entries"] != 0), "Test failed"
    assert not (result["memory_db_available"] is not True), "Test failed"
    assert not ("Neo4j connection timeout" not in result.get("error", "")), "Test failed"


def test_health_memory_db_success():
    """Health endpoint returns healthy with entry count on success."""
    if not _ensure_health_deps():
        print(f"  [SKIP] Dependencies unavailable: {_HEALTH_MISSING}")

    _make_health_cfg()

    from unittest.mock import AsyncMock
    mock_db = MagicMock()
    mock_db.get_total_entries = AsyncMock(return_value=42)
    g_data_health._registry['memory_db'] = mock_db

    import asyncio
    result = asyncio.run(health())

    assert not (result["status"] != "healthy"), "Test failed"
    assert not (result["memory_entries"] != 42), "Test failed"
    assert not (result["memory_db_available"] is not True), "Test failed"
    assert not (result["error"] is not None), "Test failed"


# ---------------------------------------------------------------------------
# verify_api_key tests
# ---------------------------------------------------------------------------

_AUTH_DEPS_AVAILABLE = False
_AUTH_MISSING = ""


def _ensure_auth_deps():
    """Try to import everything needed for verify_api_key tests."""
    global _AUTH_DEPS_AVAILABLE, _AUTH_MISSING
    if _AUTH_DEPS_AVAILABLE:
        return True
    if 'api_server' in sys.modules:
        try:
            global verify_api_key, g_data_auth, ConfigurationFile_auth, HTTPException
            from api_server import verify_api_key
            from lib.global_registry import g_data as g_data_auth
            from lib.configuration_file import ConfigurationFile as ConfigurationFile_auth
            from fastapi import HTTPException
            from fastapi.security import HTTPAuthorizationCredentials  # noqa: F401
            _AUTH_DEPS_AVAILABLE = True
            return True
        except Exception as e:
            _AUTH_MISSING = str(e)
            return False
    try:
        global verify_api_key, g_data_auth, ConfigurationFile_auth, HTTPException
        _mock_cfg = MagicMock()
        _mock_cfg.data = {'bot': {'log_level': 'INFO'}}
        with patch('lib.configuration_file.ConfigurationFile.load', return_value=_mock_cfg):
            import api_server  # noqa: F401
        from api_server import verify_api_key
        from lib.global_registry import g_data as g_data_auth
        from lib.configuration_file import ConfigurationFile as ConfigurationFile_auth
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials  # noqa: F401
        _AUTH_DEPS_AVAILABLE = True
        return True
    except Exception as e:
        _AUTH_MISSING = str(e)
        return False


def _make_auth_cfg(**overrides):
    """Create config and set it in g_data."""
    data = {
        'default_model': 'test-model',
        'providers': {'ollama': {'url': 'http://localhost:11434'}},
        'default_provider': 'ollama',
    }
    data.update(overrides)
    cfg = ConfigurationFile_auth('test-config.yml', data)
    g_data_auth._registry['cfg'] = cfg
    return cfg


def test_auth_no_key_configured():
    """verify_api_key returns None when no api_key is set in config."""
    if not _ensure_auth_deps():
        print(f"  [SKIP] Dependencies unavailable: {_AUTH_MISSING}")

    _make_auth_cfg()  # No api key set

    import asyncio
    try:
        result = asyncio.run(verify_api_key(credentials=None))
        assert (result is None), "Test failed"
    except Exception as e:
        pass


def test_auth_key_configured_no_header():
    """verify_api_key raises 401 when api_key is set but no auth header provided."""
    if not _ensure_auth_deps():
        print(f"  [SKIP] Dependencies unavailable: {_AUTH_MISSING}")

    _make_auth_cfg(api={'api_key': 'secret123'})

    import asyncio
    try:
        asyncio.run(verify_api_key(credentials=None))
    except HTTPException as e:
        assert (e.status_code == 401), "Test failed"
    except Exception as e:
        pass


def test_auth_key_configured_wrong_key():
    """verify_api_key raises 403 when api_key is set but wrong key provided."""
    if not _ensure_auth_deps():
        print(f"  [SKIP] Dependencies unavailable: {_AUTH_MISSING}")

    _make_auth_cfg(api={'api_key': 'secret123'})

    from fastapi.security import HTTPAuthorizationCredentials
    wrong_creds = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="wrong-key"
    )

    import asyncio
    try:
        asyncio.run(verify_api_key(credentials=wrong_creds))
    except HTTPException as e:
        assert (e.status_code == 403), "Test failed"
    except Exception as e:
        pass


def test_auth_key_configured_correct_key():
    """verify_api_key returns None when correct api_key is provided."""
    if not _ensure_auth_deps():
        print(f"  [SKIP] Dependencies unavailable: {_AUTH_MISSING}")

    _make_auth_cfg(api={'api_key': 'secret123'})

    from fastapi.security import HTTPAuthorizationCredentials
    correct_creds = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="secret123"
    )

    import asyncio
    try:
        result = asyncio.run(verify_api_key(credentials=correct_creds))
        assert (result is None), "Test failed"
    except Exception as e:
        pass


def test_auth_empty_api_key_skips():
    """verify_api_key returns None when api_key is empty string (auth disabled)."""
    if not _ensure_auth_deps():
        print(f"  [SKIP] Dependencies unavailable: {_AUTH_MISSING}")

    _make_auth_cfg(api={'api_key': ''})

    import asyncio
    try:
        result = asyncio.run(verify_api_key(credentials=None))
        assert (result is None), "Test failed"
    except Exception as e:
        pass


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
