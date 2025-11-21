import re, time
import pytz
from datetime import datetime

class NamiSystemPrompt:
    def __init__(self, path) -> 'NamiSystemPrompt':
        with open(path, 'r', encoding='utf-8') as f:
            self.prompt = f.read()
    
    async def parse(self):
        matches = re.findall(r'\{\{(.+?)\}\}', self.prompt)
        for match in matches:
            method_name = match.lower()
            if hasattr(self, method_name) and callable(getattr(self, method_name)):
                replacement = await getattr(self, method_name)()
                self.prompt = self.prompt.replace(f'{{{{{match}}}}}', replacement)
        return self.prompt

    async def get_prompt(self):
        return await self.parse()
    
    async def time(self):
        berlin_tz = pytz.timezone('Europe/Berlin')
        return datetime.now(berlin_tz).strftime('%H:%M:%S')

    async def date(self):
        return time.strftime('%d-%m-%Y')