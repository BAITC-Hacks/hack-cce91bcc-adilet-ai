"""One isolated HTTP request. Parent can kill slow DNS/streaming at its deadline."""
import json
import logging
import sys
import requests
from dashboard.ai.config import MAX_RESPONSE_BYTES


def request(packet):
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.post(packet['url'], json=packet['payload'],
                headers={'Authorization':'Bearer '+packet['key']}, timeout=packet['timeout'],
                allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    return {'status':response.status_code}  # Never propagate response/error bodies.
                body = bytearray()
                for chunk in response.iter_content(8192):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        return {'error':'oversize'}
                text = body.decode('utf-8').replace(packet['key'],'[REDACTED]')
                return {'status':200,'data':json.loads(text)}
    except requests.Timeout:
        return {'error':'timeout'}
    except requests.RequestException:
        return {'error':'network'}
    except (ValueError, UnicodeError):
        return {'error':'invalid_json'}


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        result = request(json.loads(sys.stdin.buffer.read(256000)))
        sys.stdout.write(json.dumps(result,ensure_ascii=False,allow_nan=False))
    except Exception:
        sys.stdout.write('{"error":"transport"}')
