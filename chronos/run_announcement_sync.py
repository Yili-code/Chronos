"""Explicitly import saved announcements into the configured canonical database."""
import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path


async def run(args):
    if not args.enable:
        print('announcement_sync=disabled')
        return 0
    from .settings import settings
    from .db import create_database
    from .announcements import AnnouncementObservationStore
    from .announcement_sync import sync_announcements
    from .announcement_scheduler import tick_announcements
    from .telegram import TelegramClient
    if not settings.telegram_chat_id or not settings.telegram_bot_token:
        print('announcement_sync=configuration_required')
        return 2
    db = create_database(settings)
    db.initialize()
    store = AnnouncementObservationStore(args.observations)
    telegram = TelegramClient(settings.telegram_bot_token)
    while True:
        now = datetime.now(settings.tz)
        result = sync_announcements(db, store, now)
        delivery = await tick_announcements(db, telegram, settings.telegram_chat_id, now)
        print(json.dumps({'announcement_sync':result, **delivery}))
        if not args.watch:
            return 0
        await asyncio.sleep(30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--observations', type=Path, default=Path('.study-data/announcements.sqlite3'))
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('announcement_sync=failed; no source data or credentials logged')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
