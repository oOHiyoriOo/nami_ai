"""
Tests for safe_edit_validator — whitelist-based path validation.
"""
import json
import pytest
from pathlib import Path

from lib.services.safe_edit_validator import (
    validate_edit_path,
    is_edit_allowed,
    _load_whitelist,
    _clear_cache,
    _WHITELIST_PATH,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the whitelist cache before each test."""
    _clear_cache()
    yield
    _clear_cache()


class TestDenyListPriority:
    """Deny-list must override allow-list."""

    def test_event_bus_is_denied(self):
        allowed, reason, reload_event = validate_edit_path("lib/services/event_bus.py")
        assert not allowed
        assert "subscriptions" in reason.lower()
        assert reload_event is None

    def test_api_server_denied(self):
        allowed, _, _ = validate_edit_path("api_server.py")
        assert not allowed

    def test_global_registry_denied(self):
        allowed, _, _ = validate_edit_path("lib/global_registry.py")
        assert not allowed

    def test_ai_providers_denied(self):
        allowed, _, _ = validate_edit_path("lib/ai_providers/ollama_provider.py")
        assert not allowed

    def test_config_yml_denied(self):
        allowed, _, _ = validate_edit_path("config.yml")
        assert not allowed

    def test_system_prompt_denied(self):
        allowed, _, _ = validate_edit_path("system_prompt/nami.md")
        assert not allowed

    def test_nami_safety_files_denied(self):
        allowed, _, _ = validate_edit_path(".nami_change_session")
        assert not allowed

    def test_ai_pipeline_denied(self):
        allowed, _, _ = validate_edit_path("lib/services/ai_pipeline.py")
        assert not allowed


class TestAllowListMatching:
    """Glob patterns in the allow-list must match correctly."""

    def test_ollama_tool_allowed(self):
        allowed, reason, reload_event = validate_edit_path("OllamaTools/begin_session.py")
        assert allowed
        assert reload_event == "system.reload_tools"

    def test_heartbeat_module_allowed(self):
        allowed, reason, reload_event = validate_edit_path(
            "lib/services/heartbeat_modules/curiosity.py"
        )
        assert allowed
        assert "heartbeat_modules" in reload_event

    def test_context_builder_allowed(self):
        allowed, reason, reload_event = validate_edit_path(
            "lib/services/context_builder.py"
        )
        assert allowed
        assert "context_builder" in reload_event

    def test_memory_extractor_allowed(self):
        allowed, _, _ = validate_edit_path("lib/services/memory_extractor.py")
        assert allowed

    def test_memory_service_allowed(self):
        allowed, _, _ = validate_edit_path("lib/services/memory_service.py")
        assert allowed

    def test_vision_service_allowed(self):
        allowed, _, _ = validate_edit_path("lib/services/vision_service.py")
        assert allowed

    def test_notification_pipeline_allowed(self):
        allowed, _, _ = validate_edit_path("lib/services/notification_pipeline.py")
        assert allowed

    def test_tool_context_allowed(self):
        allowed, reason, reload_event = validate_edit_path("lib/services/tool_context.py")
        assert allowed
        assert reload_event == "system.reload_tools"

    def test_tool_executor_allowed_with_null_reload(self):
        allowed, reason, reload_event = validate_edit_path("lib/services/tool_executor.py")
        assert allowed
        assert reload_event is None

    def test_utils_module_allowed(self):
        allowed, _, reload_event = validate_edit_path("lib/utils/dynamic_loader.py")
        assert allowed
        assert "lib.utils" in reload_event

    def test_mcp_client_allowed(self):
        allowed, _, _ = validate_edit_path("lib/mcp_client.py")
        assert allowed

    def test_requirements_txt_allowed(self):
        allowed, _, reload_event = validate_edit_path("requirements.txt")
        assert allowed
        assert reload_event == "pip_install"

    def test_docs_markdown_allowed(self):
        allowed, _, reload_event = validate_edit_path("docs/ARCHITECTURE.md")
        assert allowed
        assert reload_event is None

    def test_test_file_allowed(self):
        allowed, _, reload_event = validate_edit_path("tests/test_event_bus.py")
        assert allowed
        assert reload_event is None


class TestImplicitDeny:
    """Unlisted paths must be implicitly denied."""

    def test_unlisted_lib_file_denied(self):
        allowed, reason, _ = validate_edit_path("lib/services/sandbox_manager.py")
        assert not allowed
        assert "not in the edit whitelist" in reason

    def test_unlisted_root_file_denied(self):
        allowed, reason, _ = validate_edit_path("docker-compose.yml")
        assert not allowed
        assert "not in the edit whitelist" in reason

    def test_scripts_denied(self):
        allowed, reason, _ = validate_edit_path("scripts/nami_start.sh")
        assert not allowed
        assert "not in the edit whitelist" in reason

    def test_adapter_file_denied(self):
        allowed, reason, _ = validate_edit_path(
            "lib/services/adapter_manager.py"
        )
        assert not allowed


class TestErrorMessages:
    """Denial error messages must include the list of allowed paths."""

    def test_implicit_deny_lists_allowed_paths(self):
        _, reason, _ = validate_edit_path("lib/services/sandbox_manager.py")
        assert "OllamaTools/*.py" in reason
        assert "lib/services/heartbeat_modules/*.py" in reason
        assert "lib/services/context_builder.py" in reason

    def test_explicit_deny_has_specific_reason(self):
        _, reason, _ = validate_edit_path("lib/services/event_bus.py")
        assert "kill" in reason.lower()

    def test_outside_project_root(self):
        allowed, reason, _ = validate_edit_path("/etc/passwd")
        assert not allowed
        assert "outside" in reason.lower()


class TestConvenienceFunction:
    """is_edit_allowed shortcut."""

    def test_is_edit_allowed_true(self):
        assert is_edit_allowed("OllamaTools/begin_session.py")

    def test_is_edit_allowed_false(self):
        assert not is_edit_allowed("lib/services/event_bus.py")


class TestWhitelistLoading:
    """Loading and schema validation edge cases."""

    def test_loads_valid_whitelist(self):
        data = _load_whitelist()
        assert data["version"] == 1
        assert isinstance(data["allow"], list)
        assert isinstance(data["deny"], list)
        assert len(data["allow"]) > 0
        assert len(data["deny"]) > 0

    def test_cached_on_second_call(self):
        first = _load_whitelist()
        second = _load_whitelist()
        assert first is second

    def test_version_mismatch_is_safe(self, tmp_path):
        bad_file = tmp_path / "safe_edit_paths.json"
        bad_file.write_text(json.dumps({"version": 999, "allow": [], "deny": []}))

        import lib.services.safe_edit_validator as mod
        original = mod._WHITELIST_PATH
        mod._WHITELIST_PATH = bad_file
        _clear_cache()

        try:
            data = _load_whitelist()
            assert data["allow"] == []  # fallback to empty
        finally:
            mod._WHITELIST_PATH = original
            _clear_cache()

    def test_missing_keys_is_safe(self, tmp_path):
        bad_file = tmp_path / "safe_edit_paths.json"
        bad_file.write_text(json.dumps({"version": 1}))

        import lib.services.safe_edit_validator as mod
        original = mod._WHITELIST_PATH
        mod._WHITELIST_PATH = bad_file
        _clear_cache()

        try:
            data = _load_whitelist()
            assert data["allow"] == []
        finally:
            mod._WHITELIST_PATH = original
            _clear_cache()

    def test_invalid_json_is_safe(self, tmp_path):
        bad_file = tmp_path / "safe_edit_paths.json"
        bad_file.write_text("not valid json {{{")

        import lib.services.safe_edit_validator as mod
        original = mod._WHITELIST_PATH
        mod._WHITELIST_PATH = bad_file
        _clear_cache()

        try:
            data = _load_whitelist()
            assert data["allow"] == []
        finally:
            mod._WHITELIST_PATH = original
            _clear_cache()

    def test_missing_file_is_safe(self, tmp_path):
        import lib.services.safe_edit_validator as mod
        original = mod._WHITELIST_PATH
        mod._WHITELIST_PATH = tmp_path / "nonexistent.json"
        _clear_cache()

        try:
            data = _load_whitelist()
            assert data["allow"] == []
            # validate_edit_path should deny everything
            allowed, _, _ = validate_edit_path("OllamaTools/begin_session.py")
            assert not allowed
        finally:
            mod._WHITELIST_PATH = original
            _clear_cache()


class TestAbsolutePaths:
    """validate_edit_path handles absolute paths."""

    def test_absolute_project_path(self):
        """Absolute paths within the project should resolve relative."""
        project_root = Path("/workspace/project/nami_ai")
        abs_path = project_root / "OllamaTools/begin_session.py"
        if abs_path.exists():
            allowed, _, _ = validate_edit_path(str(abs_path))
            assert allowed
