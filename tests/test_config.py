"""
Test script for configuration file handling
"""

import sys
from pathlib import Path
import tempfile

import pytest
import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.configuration_file import ConfigurationFile, ConfigValidationError


def _make_valid_config(**overrides) -> dict:
    """Create a minimal config dict that passes validation."""
    cfg = {
        'default_model': 'test-model',
        'providers': {'test-provider': {'url': 'http://localhost:11434'}},
        'default_provider': 'test-provider',
    }
    cfg.update(overrides)
    return cfg


def _write_temp_config(data: dict) -> str:
    """Write a dict to a temporary YAML file and return the path."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
        yaml.safe_dump(data, f)
        return f.name


def test_config_load_valid():
    """Test loading a valid YAML configuration"""

    config_data = _make_valid_config(
        app={'name': 'test_app', 'version': '1.0.0'},
        settings={'debug': True, 'timeout': 30},
    )
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        assert config.data == config_data
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_config_save():
    """Test saving configuration"""

    config_data = _make_valid_config(
        test={'key': 'value', 'number': 42},
    )
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        config.data = config_data
        config.save()

        config2 = ConfigurationFile.load(temp_path)
        assert config2.data == config_data
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_config_nested_access():
    """Test accessing nested configuration values"""

    config_data = _make_valid_config(
        database={
            'host': 'localhost',
            'port': 5432,
            'credentials': {'user': 'admin', 'password': 'secret'},
        },
    )
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        assert config.data['database']['host'] == 'localhost'
        assert config.data['database']['credentials']['user'] == 'admin'
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_config_reload_modified():
    """Test reloading configuration after file modification"""

    original_data = _make_valid_config(key='original', nested={'a': 1})
    modified_data = _make_valid_config(key='modified', nested={'a': 2}, new_key='added')
    temp_path = _write_temp_config(original_data)

    try:
        config = ConfigurationFile.load(temp_path)
        assert config.data == original_data

        with open(temp_path, 'w') as f:
            yaml.safe_dump(modified_data, f)

        result = config.reload()
        assert result == modified_data
        assert config.data == modified_data
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_config_reload_file_deleted():
    """Test reload raises FileNotFoundError when config file is deleted"""

    config_data = _make_valid_config(key='value')
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        Path(temp_path).unlink()

        with pytest.raises(FileNotFoundError):
            config.reload()
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_config_reload_invalid_yaml():
    """Test reload raises YAMLError when config file has invalid YAML"""

    config_data = _make_valid_config(key='value')
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        with open(temp_path, 'w') as f:
            f.write("invalid: yaml: ::: broken\nindentation: [unclosed\n")

        with pytest.raises(yaml.YAMLError):
            config.reload()
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_config_reload_empty_yaml():
    """Test reload raises ConfigValidationError when config file is empty

    An empty file causes yaml.safe_load() to return None, which
    reload() must convert to {} via ``or {}`` before passing to
    _validate().  Without the fallback, None.get() crashes with
    AttributeError instead of raising a proper ConfigValidationError.
    """

    config_data = _make_valid_config(key='value')
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        original_data = config.data

        # Write an empty file
        with open(temp_path, 'w') as f:
            f.write('')

        with pytest.raises(ConfigValidationError, match="default_model|default_provider"):
            config.reload()
        assert config.data == original_data, "self.data was corrupted by failed reload"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_reload_validation_missing_model():
    """Test reload raises ConfigValidationError when model is removed

    Verifies that reload() runs validation and the original self.data
    is preserved when validation fails — no silent corruption.
    """

    config_data = _make_valid_config()
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        original_data = config.data

        # Write invalid config (no default_model)
        bad_data = _make_valid_config()
        del bad_data['default_model']
        with open(temp_path, 'w') as f:
            yaml.safe_dump(bad_data, f)

        with pytest.raises(ConfigValidationError, match="default_model is required"):
            config.reload()
        assert config.data == original_data, "self.data was corrupted by failed reload"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_reload_validation_invalid_provider():
    """Test reload raises ConfigValidationError when provider is invalid

    Verifies that reload() runs validation and the original self.data
    is preserved when validation fails.
    """

    config_data = _make_valid_config()
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        original_data = config.data

        # Write invalid config (default_provider not in providers)
        bad_data = _make_valid_config(default_provider='ollama')
        with open(temp_path, 'w') as f:
            yaml.safe_dump(bad_data, f)

        with pytest.raises(ConfigValidationError, match="default_provider.*not found in providers"):
            config.reload()
        assert config.data == original_data, "self.data was corrupted by failed reload"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_reload_validation_missing_discord_token():
    """Test reload raises ConfigValidationError when discord token missing

    Verifies that reload() runs validation and the original self.data
    is preserved when validation fails.
    """

    config_data = _make_valid_config()
    temp_path = _write_temp_config(config_data)

    try:
        config = ConfigurationFile.load(temp_path)
        original_data = config.data

        # Write invalid config (discord enabled but no token)
        bad_data = _make_valid_config(
            adapters={'discord': {'enabled': True, 'token': ''}}
        )
        with open(temp_path, 'w') as f:
            yaml.safe_dump(bad_data, f)

        with pytest.raises(ConfigValidationError, match="adapters.discord.token is required"):
            config.reload()
        assert config.data == original_data, "self.data was corrupted by failed reload"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_validation_missing_model():
    """Test validation fails when default_model is missing"""

    config_data = _make_valid_config()
    del config_data['default_model']
    temp_path = _write_temp_config(config_data)

    try:
        with pytest.raises(ConfigValidationError, match="default_model is required"):
            ConfigurationFile.load(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_validation_invalid_provider():
    """Test validation fails when default_provider not in providers"""

    config_data = _make_valid_config(default_provider='ollama')
    temp_path = _write_temp_config(config_data)

    try:
        with pytest.raises(ConfigValidationError, match="default_provider.*not found in providers"):
            ConfigurationFile.load(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_validation_missing_discord_token():
    """Test validation fails when discord enabled but no token"""

    config_data = _make_valid_config(
        adapters={'discord': {'enabled': True, 'token': ''}}
    )
    temp_path = _write_temp_config(config_data)

    try:
        with pytest.raises(ConfigValidationError, match="adapters.discord.token is required"):
            ConfigurationFile.load(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_validation_discord_disabled_no_token():
    """Test validation passes when discord disabled without token"""

    config_data = _make_valid_config(
        adapters={'discord': {'enabled': False}}
    )
    temp_path = _write_temp_config(config_data)

    try:
        ConfigurationFile.load(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_validation_discord_with_token():
    """Test validation passes when discord enabled with token"""

    config_data = _make_valid_config(
        adapters={'discord': {'enabled': True, 'token': 'VALID_TOKEN'}}
    )
    temp_path = _write_temp_config(config_data)

    try:
        ConfigurationFile.load(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)
