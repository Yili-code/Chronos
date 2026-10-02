"""Send the requested Phase 0 learning document to configured Sisyphus chat.

Secrets remain in memory. Never print request exceptions, URLs or responses.
"""
import logging
import argparse
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('0', '1'), default='0')
    phase = parser.parse_args().phase
    logging.disable(logging.CRITICAL)
    try:
        settings = {}
        for line in (Path.home() / '.codex/sisyphus/telegram.env').read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                settings[key.strip()] = value.strip().strip('\"').strip("'")
        token = settings['TELEGRAM_BOT_TOKEN']
        chat = settings['TELEGRAM_CHAT_ID']
        filename = 'study-module-interview-prep.md' if phase == '0' else 'study-module-phase1-interview-prep.md'
        document = Path(__file__).resolve().parents[1] / 'docs' / filename
        with document.open('rb') as stream:
            response = httpx.post(
                f'https://api.telegram.org/bot{token}/sendDocument',
                data={'chat_id': chat, 'caption': f'Chronos Phase {phase} 面試準備文件：完整 Markdown 附件。'},
                files={'document': (document.name, stream, 'text/markdown')}, timeout=40,
            )
        result = response.json()
        if response.is_success and result.get('ok') is True and result.get('result', {}).get('document'):
            print('document_delivery=confirmed')
            return 0
        print('document_delivery=unconfirmed')
    except Exception:
        print('document_delivery=unknown; do not blindly retry')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
