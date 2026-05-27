import logging
from typing import Any, Callable

class GlobalRegistry:
    """
    A simple registry for managing globally shared singleton instances.
    Uses a unique key to store and retrieve instances.
    Ensures that only one instance is created per key.
    """
    _instance = None  # For the singleton pattern of the registry itself

    def __new__(cls, *args, **kwargs):
        # Ensures only one instance of the registry itself exists
        if cls._instance is None:
            cls._instance = super(GlobalRegistry, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self._registry: dict[str, Any] = {}
        self._initialized = True
        logging.info("GlobalRegistry initialized.")

    def get_or_create(self, key: str, instance_factory: Callable[[], Any], *factory_args, **factory_kwargs) -> Any:
        """
        Retrieves an instance by key or creates it if it doesn't exist.

        Args:
            key (str): The unique key for the instance (e.g., "databases.memory", "models.embedding").
                       Dot notation is just a naming convention; internally it's treated as a flat key.
            instance_factory (Callable[[], Any]): A function or class constructor
                                                  called to create the instance,
                                                  *only* if the key doesn't exist yet.
            *factory_args: Positional arguments passed to instance_factory.
            **factory_kwargs: Keyword arguments passed to instance_factory.

        Returns:
            Any: The existing or newly created instance.
        """
        if key not in self._registry:
            logging.info(f"Creating new instance for key '{key}'...")
            try:
                # Create the instance only if it's not in the registry
                self._registry[key] = instance_factory(*factory_args, **factory_kwargs)
                logging.info(f"Instance created and registered for key '{key}'.")
            except Exception as e:
                 logging.error(f"Failed to create instance for key '{key}': {e}", exc_info=True)
                 # Re-raise error so the application knows something went wrong
                 raise
        else:
            logging.debug(f"Returning existing instance for key '{key}'.")

        return self._registry[key]

    def register(self, key: str, value: Any) -> None:
        """Store *value* under *key*, overwriting any existing entry.

        Unlike :meth:`get_or_create`, this method always writes the value
        (no factory, no singleton guard).  Use it to register plain objects
        such as asyncio.Lock instances that are created outside the registry.

        Args:
            key:   Registry key.
            value: Value to store.
        """
        self._registry[key] = value

    def get(self, key: str) -> Any | None:
        """
        Retrieves an instance by key without creating it.

        Args:
            key (str): The key of the instance to retrieve.

        Returns:
            Optional[Any]: The instance if it exists, otherwise None.
        """
        return self._registry.get(key)

    def exists(self, key: str) -> bool:
        """Checks if a key exists in the registry."""
        return key in self._registry

    def clear_key(self, key: str):
        """Removes a key and its associated instance from the registry."""
        if key in self._registry:
            logging.info(f"Removing instance for key '{key}' from registry.")
            # Optional: Check if the instance has a close() method and call it
            instance = self._registry[key]
            if hasattr(instance, 'close') and callable(getattr(instance, 'close')):
                try:
                    logging.debug(f"Calling close() method for instance with key '{key}'.")
                    instance.close()
                except Exception as e:
                    logging.warning(f"Error calling close() for instance '{key}': {e}", exc_info=True)
            del self._registry[key]
        else:
             logging.debug(f"Key '{key}' not found in registry for clearing.")

    def clear_all(self):
        """Clears the entire registry and calls close() if available."""
        logging.warning("Clearing all instances from GlobalRegistry.")
        keys_to_clear = list(self._registry.keys())  # Copy keys since we're modifying the dict
        for key in keys_to_clear:
            self.clear_key(key)
        logging.info("GlobalRegistry cleared.")

g_data = GlobalRegistry()