import yaml


class ConfigValidationError(ValueError):
    """Raised when the configuration fails validation at load time."""


class ConfigurationFile:
    """Load, validate, reload, and persist YAML configuration.

    This is the canonical configuration object shared across all modules
    via the global registry (``g_data``).  YAML files are loaded through the
    factory method ``load()`` and must satisfy the validation rules enforced
    by ``_validate()``.

    **Key Methods**
        * ``load(path)`` — Factory method that parses a YAML file, validates
          the result, and returns a ``ConfigurationFile`` instance.
        * ``reload()`` — Re-read the file from disk, re-validate, and update
          the in-memory data.
        * ``save()`` — Persist the current in-memory configuration back to
          disk as YAML.
        * ``_validate(data)`` — Static validation routine called on every
          load and reload.

    **Validation Rules**
        * ``default_provider`` must reference an entry in the ``providers``
          dictionary.
        * ``default_model`` is required.
        * If ``adapters.discord.enabled`` is true, ``adapters.discord.token``
          must also be set.

    Raises:
        ConfigValidationError: If any validation check fails during
            :meth:`load` or :meth:`reload`.
    """

    def __init__(self, path: str, data: dict | None = None):
        """
        Initialize configuration. Use ConfigurationFile.load(path) for file loading.
        
        Args:
            path: Path to the config file
            data: Pre-loaded config data (optional, for factory pattern)
        """
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path: str) -> 'ConfigurationFile':
        """
        Factory method to load configuration from file.

        Args:
            path: Path to the YAML config file

        Returns:
            ConfigurationFile instance with loaded data

        Raises:
            ConfigValidationError: If configuration fails validation
        """
        with open(path, 'r', encoding='utf-8') as config_file:
            data = yaml.safe_load(config_file) or {}

        cls._validate(data)
        return cls(path, data)

    def reload(self) -> dict:
        """Reload configuration from file.

        Raises:
            ConfigValidationError: If reloaded configuration fails validation
        """
        with open(self.path, 'r', encoding='utf-8') as config_file:
            new_data = yaml.safe_load(config_file) or {}

        self.__class__._validate(new_data)
        self.data = new_data
        return self.data

    @staticmethod
    def _validate(data: dict) -> None:
        """Validate configuration data.

        Args:
            data: Configuration dict to validate

        Raises:
            ConfigValidationError: If configuration fails validation
        """
        # Validate provider references
        providers = data.get('providers', {})
        default_provider = data.get('default_provider', 'ollama')
        if default_provider and default_provider not in providers:
            raise ConfigValidationError(
                f"default_provider '{default_provider}' not found in providers"
            )

        # Validate model references
        default_model = data.get('default_model')
        if not default_model:
            raise ConfigValidationError("default_model is required")

        # Validate Discord adapter config
        discord_cfg = data.get('adapters', {}).get('discord', {})
        if discord_cfg.get('enabled'):
            if not discord_cfg.get('token'):
                raise ConfigValidationError(
                    "adapters.discord.token is required when adapters.discord.enabled is true"
                )

    def save(self) -> None:
        """Persist current configuration data to the YAML file on disk."""
        with open(self.path, 'w', encoding='utf-8') as config_file:
            yaml.safe_dump(self.data, config_file)