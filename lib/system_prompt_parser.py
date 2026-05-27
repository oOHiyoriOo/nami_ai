import re
import pytz
from datetime import datetime

class NamiSystemPrompt:
    """
    Loads and parses Markdown-based system prompts with template variable resolution.

    Supports ``{{time}}`` and ``{{date}}`` template variables that are resolved at
    query time using the configured IANA timezone (default: Europe/Berlin). Any
    template variable ``{{<name>}}`` is resolved by calling the corresponding
    ``self.<name>()`` async method if it exists.
    """

    def __init__(self, path: str, prompt: str | None = None, tz_name: str = 'Europe/Berlin'):
        """
        Initialize system prompt, loading from file if no prompt content is provided.
        
        Args:
            path: Path to the prompt file
            prompt: Pre-loaded prompt content (optional — auto-loads from path if omitted)
            tz_name: IANA timezone name for {{time}}/{{date}} templates (default: Europe/Berlin)
        """
        self.path = path
        if prompt is None:
            with open(path, 'r', encoding='utf-8') as f:
                prompt = f.read()
        self._raw_prompt = prompt
        self.tz = pytz.timezone(tz_name)

    @classmethod
    def load(cls, path: str, tz_name: str = 'Europe/Berlin') -> 'NamiSystemPrompt':
        """
        Factory method to load system prompt from file.
        
        Args:
            path: Path to the markdown prompt file
            tz_name: IANA timezone name for {{time}}/{{date}} templates (default: Europe/Berlin)
            
        Returns:
            NamiSystemPrompt instance with loaded content
        """
        with open(path, 'r', encoding='utf-8') as f:
            prompt = f.read()
        return cls(path, prompt, tz_name)
    
    async def parse(self):
        """
        Resolve all ``{{<name>}}`` template variables in the raw prompt.

        Each template variable maps to a corresponding async method on this
        instance (e.g. ``{{time}}`` → ``self.time()``). The resolved values
        replace their templates in-place and the fully-resolved prompt string
        is returned.

        Returns:
            The prompt string with all template variables resolved.
        """
        result = self._raw_prompt
        for match in re.findall(r'\{\{(.+?)\}\}', result):
            method_name = match.lower()
            if hasattr(self, method_name) and callable(getattr(self, method_name)):
                replacement = await getattr(self, method_name)()
                result = result.replace(f'{{{{{match}}}}}', replacement)
        return result

    async def get_prompt(self):
        """
        Return the fully-resolved prompt with all template variables substituted.

        Convenience wrapper around :meth:`parse`.

        Returns:
            The prompt string with all ``{{<name>}}`` template variables resolved.
        """
        return await self.parse()
    
    async def time(self):
        """
        Return the current wall-clock time in the configured IANA timezone.

        Used as the ``{{time}}`` template resolver. Format: ``HH:MM:SS``.

        Returns:
            Current time as a string (e.g. ``"14:05:32"``).
        """
        return datetime.now(self.tz).strftime('%H:%M:%S')

    async def date(self):
        """
        Return the current date in the configured IANA timezone.

        Used as the ``{{date}}`` template resolver. Format: ``DD-MM-YYYY``.

        Returns:
            Current date as a string (e.g. ``"08-05-2026"``).
        """
        return datetime.now(self.tz).strftime('%d-%m-%Y')