from dataclasses import dataclass, field, asdict
import hashlib
import json
import os
from urllib.parse import urlsplit

PROMPT_VERSION = 'investigator-v1'
TOOLS_VERSION = 'bounded-tools-v1'
RULES_VERSION = 'evidence-validation-v1'
MAX_CONTEXT_BYTES = 96000
MAX_TOOL_BYTES = 14000
MAX_RESPONSE_BYTES = 128000


class AIError(ValueError):
    """Only fixed, public-safe messages cross the UI boundary."""


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default='', repr=False)
    base_url: str = 'https://api.openai.com/v1'
    model: str = ''
    timeout: float = 60
    max_tool_calls: int = 8
    max_output_tokens: int = 3000
    enabled: bool = False

    @classmethod
    def from_env(cls):
        try:
            return cls(api_key=os.getenv('AI_API_KEY', '').strip(),
                base_url=os.getenv('AI_BASE_URL', 'https://api.openai.com/v1').strip().rstrip('/'),
                model=os.getenv('AI_MODEL', '').strip(),
                timeout=float(os.getenv('AI_TIMEOUT_SECONDS', '60')),
                max_tool_calls=int(os.getenv('AI_MAX_TOOL_CALLS', '8')),
                max_output_tokens=int(os.getenv('AI_MAX_OUTPUT_TOKENS', '3000')),
                enabled=os.getenv('AI_ENABLED', 'false').lower() in ('true', '1', 'yes'))
        except ValueError:
            raise AIError('Проверьте числовые настройки AI_TIMEOUT_SECONDS, AI_MAX_TOOL_CALLS и AI_MAX_OUTPUT_TOKENS.') from None

    def validate(self):
        if not self.enabled:
            raise AIError('AI выключен. Для подключения задайте AI_ENABLED=true.')
        if not self.api_key:
            raise AIError('Добавьте AI_API_KEY в окружение сервера.')
        if not self.model:
            raise AIError('Укажите AI_MODEL — идентификатор модели вашего провайдера.')
        try:
            url = urlsplit(self.base_url)
            _ = url.port
        except ValueError:
            raise AIError('Проверьте формат AI_BASE_URL.') from None
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise AIError('AI_BASE_URL должен быть HTTPS-адресом API без пароля, параметров и фрагмента.')
        if self.api_key in self.base_url or self.api_key in self.model or len(self.model) > 120:
            raise AIError('Проверьте AI_BASE_URL и AI_MODEL: секрет должен находиться только в AI_API_KEY.')
        if not 1 <= self.timeout <= 180 or not 3 <= self.max_tool_calls <= 12 or not 512 <= self.max_output_tokens <= 8000:
            raise AIError('Лимиты AI: дедлайн 1–180 секунд, инструменты 3–12, ответ 512–8000 токенов.')

    def public(self):
        return {k: v for k, v in asdict(self).items() if k != 'api_key'}

    def cache_key(self, data_hash, gid):
        value = dict(data_hash=data_hash, gid=gid, settings=self.public(),
            prompt=PROMPT_VERSION, tools=TOOLS_VERSION, rules=RULES_VERSION)
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
