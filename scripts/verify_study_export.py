"""One explicit live owner-only Markdown export; no polling or webhook change.

Run once. Unknown delivery must not trigger blind reruns. Only synthetic text is
sent; API responses, tokens, chat identifiers and message IDs are never printed.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import tempfile
from uuid import uuid4

from chronos.db import Database
from chronos.note_record import NoteRecord, NoteSource
from chronos.note_delivery import export_note
from chronos.settings import settings
from chronos.telegram import TelegramClient


async def main():
    logging.disable(logging.CRITICAL)
    evidence = {"synthetic_only": True, "canonical_export_confirmed": False, "duplicate_suppressed": False}
    try:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise ValueError("configuration unavailable")
        now = datetime.now(timezone.utc)
        content = "# Chronos Phase 2 export verification\n\n這是合成測試文件，不是課程摘要。\n\nUTF-8 check: 中文、English、🙂。\n"
        fingerprint = hashlib.sha256(uuid4().bytes).hexdigest()
        with tempfile.TemporaryDirectory(prefix="chronos-export-verify-") as directory:
            db = Database(Path(directory) / "verify.sqlite3")
            db.initialize()
            note = NoteRecord(content_fingerprint=fingerprint, course_id="synthetic", course="Integration test",
                class_date=now.date(), reported_progress="Synthetic export only", markdown=content,
                sources=[NoteSource(source_id="synthetic", filename="synthetic.pdf", sha256="0" * 64, page_count=1)],
                model="none-export-test", prompt_version="none", created_at=now)
            db.save_study_note(note)
            bot = TelegramClient(settings.telegram_bot_token)
            args = dict(chat_id=settings.telegram_chat_id, update_id=1, fingerprint=fingerprint, now=now)
            result = await export_note(db, bot, **args)
            evidence["delivery_status"] = result
            evidence["canonical_export_confirmed"] = result == "sent"
            if result == "sent":
                # Replace the network method: this second invocation must not send.
                async def forbidden_send(*_args, **_kwargs):
                    raise AssertionError("unexpected repeated delivery")
                bot.send_document = forbidden_send
                evidence["duplicate_suppressed"] = await export_note(db, bot, **args) == "sent"
    except Exception as error:
        evidence["error_type"] = type(error).__name__
    print(json.dumps(evidence))
    return 0 if evidence["canonical_export_confirmed"] and evidence["duplicate_suppressed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
