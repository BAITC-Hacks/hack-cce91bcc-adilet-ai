"""OpenAI-compatible Chat Completions transport; no investigation logic here."""
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from dashboard.ai.config import AIError, MAX_CONTEXT_BYTES, MAX_RESPONSE_BYTES
from dashboard.ai.evidence import canonical


@dataclass
class Reply:
    content: str
    usage: dict | None = None


def isolated_request(packet, timeout):
    process = subprocess.Popen([sys.executable,'-m','dashboard.ai.http_worker'],
        cwd=Path(__file__).resolve().parents[2], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        output, _ = process.communicate(canonical(packet).encode(), timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise AIError('Истёк общий дедлайн расследования.') from None
    if process.returncode or len(output)>MAX_RESPONSE_BYTES*2:
        raise AIError('Сетевой процесс не смог получить ответ API.')
    try:
        return json.loads(output)
    except (ValueError, UnicodeError):
        raise AIError('API вернул неверный JSON.') from None


class OpenAICompatible:
    def __init__(self, settings, request_fn=isolated_request):
        self.settings = settings
        self.request_fn = request_fn
        self.retries_left = 2  # Total per investigation, not per call.

    def complete(self, messages, deadline):
        self.settings.validate()
        payload = {'model':self.settings.model, 'messages':messages,
            'response_format':{'type':'json_object'}, 'max_completion_tokens':self.settings.max_output_tokens}
        if len(canonical(payload).encode()) > MAX_CONTEXT_BYTES:
            raise AIError('Достигнут лимит входного контекста AI.')
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise AIError('Истёк общий дедлайн расследования.')
            packet = {'url':self.settings.base_url+'/chat/completions',
                'key':self.settings.api_key,'payload':payload,'timeout':remaining}
            try:
                result = self.request_fn(packet, remaining)
            except AIError:
                raise
            except Exception:
                raise AIError('Не удалось выполнить запрос к API.') from None
            if time.monotonic() >= deadline:
                raise AIError('Истёк общий дедлайн расследования.')
            if result.get('error')=='timeout':
                raise AIError('Истёк таймаут запроса к API.')
            transient = result.get('status') in (429,500,502,503,504) or result.get('error')=='network'
            if transient and self.retries_left:
                self.retries_left -= 1
                delay = (2-self.retries_left)*.25
                if time.monotonic()+delay >= deadline:
                    raise AIError('Истёк общий дедлайн расследования.')
                time.sleep(delay)
                continue
            if result.get('status') != 200:
                if result.get('status') == 429:
                    raise AIError('Провайдер ограничил частоту запросов; повторы исчерпаны.')
                raise AIError('API недоступен или вернул неподдерживаемый ответ. Проверьте модель, ключ и endpoint на сервере.')
            try:
                data = result['data']
                content = data['choices'][0]['message']['content']
                if not isinstance(content,str) or len(content.encode()) > MAX_RESPONSE_BYTES:
                    raise ValueError()
                content = content.replace(self.settings.api_key, '[REDACTED]')
                usage = data.get('usage')
                if not isinstance(usage,dict):
                    usage = None
                else:
                    usage = {k:v for k,v in usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0}
                return Reply(content, usage or None)
            except (KeyError,IndexError,TypeError,ValueError):
                raise AIError('API не вернул текстовый ответ в формате Chat Completions.') from None
