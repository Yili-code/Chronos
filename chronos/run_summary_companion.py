"""Explicit local worker launcher. Never starts from importing this module."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re


def course_mapping(value):
    try:
        mapping = json.loads(value)
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError()
        if any(not isinstance(key, str) or not isinstance(item, str) or not re.fullmatch(r"[0-9]{1,20}", item)
               for key, item in mapping.items()):
            raise ValueError()
        from .course_tracking import COURSE_SCHEDULE
        if not set(mapping) <= {slot.key for slot in COURSE_SCHEDULE}:
            raise ValueError()
        return mapping
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("Provide a JSON object mapping known course keys to numeric TronClass IDs") from None


def parser():
    result = argparse.ArgumentParser(description="Process confirmed selections using local PDFs and the configured database")
    result.add_argument("--enable", action="store_true")
    result.add_argument("--check", action="store_true", help="Inspect local prerequisites only; no writes or network calls")
    result.add_argument("--firestore-project", help="Explicit shared Firestore project; requires database and prefix")
    result.add_argument("--firestore-database")
    result.add_argument("--firestore-prefix")
    result.add_argument("--free-tier-confirmed", action="store_true")
    result.add_argument("--course-map", type=course_mapping)
    result.add_argument("--pdf-directory", type=Path, default=Path(".study-data/pdfs"))
    result.add_argument("--catalog-path", type=Path, default=Path(".study-data/catalog.sqlite3"))
    result.add_argument("--watch", action="store_true", help="Repeat until interrupted; default is one pass")
    return result


def routed_settings(config, args):
    route = (args.firestore_project, args.firestore_database, args.firestore_prefix)
    if not any(value is not None for value in route):
        return config
    if not all(isinstance(value, str) and value.strip() and '/' not in value for value in route):
        raise ValueError("explicit Firestore project, database and prefix are required together")
    return config.model_copy(update={"database_backend": "firestore",
        "firestore_project_id": route[0], "firestore_database": route[1],
        "firestore_collection_prefix": route[2]})


def preflight(config, args):
    """Safe configuration facts, not a claim of remote readiness."""
    return {
        "mode": "read_only_preflight",
        "database_backend": config.database_backend,
        "sqlite_file_exists": config.database_path.is_file() if config.database_backend == "sqlite" else None,
        "firestore_project_explicit": bool(config.firestore_project_id),
        "telegram_configured": bool(config.telegram_chat_id and config.telegram_bot_token),
        "provider_key_configured": bool(config.gemini_api_key),
        "study_model_is_lite": config.study_gemini_model == "gemini-3.1-flash-lite",
        "catalog_file_exists": args.catalog_path.is_file(),
        "pdf_directory_exists": args.pdf_directory.is_dir(),
        "course_mapping_supplied": bool(args.course_map),
        "free_tier_confirmed": args.free_tier_confirmed,
        "remote_readiness": "not_checked",
    }


async def run(args):
    if args.check:
        from .settings import settings
        print(json.dumps(preflight(routed_settings(settings, args), args)))
        return 0
    if not args.enable:
        print("summary_companion=disabled")
        return 0
    if not args.free_tier_confirmed or not args.course_map:
        print("summary_companion=configuration_required")
        return 2
    from .settings import settings
    settings = routed_settings(settings, args)
    from .db import create_database
    from .gemini_summary import GeminiSummary, PROMPT_VERSION
    from .pdf_store import PdfStore
    from .telegram import TelegramClient
    from .summary_companion import run_summary_pass
    from .catalog_prompt import prompt_observed_catalogs
    from .material_bridge import MaterialObservationStore
    if not settings.telegram_chat_id or not settings.telegram_bot_token or not settings.gemini_api_key:
        print("summary_companion=configuration_required")
        return 2
    db = create_database(settings)
    db.initialize()
    study_config = settings.model_copy(update={"gemini_model": settings.study_gemini_model})
    generator = GeminiSummary(study_config, free_tier_confirmed=True)
    bot = TelegramClient(settings.telegram_bot_token)
    store = PdfStore(args.pdf_directory)
    while True:
        await prompt_observed_catalogs(db, bot, MaterialObservationStore(args.catalog_path),
            owner_chat_id=settings.telegram_chat_id, course_mapping=args.course_map, now=datetime.now(settings.tz))
        result = await run_summary_pass(db, generator, bot, store,
            owner_chat_id=settings.telegram_chat_id, course_mapping=args.course_map,
            model=study_config.gemini_model, prompt_version=PROMPT_VERSION,
            now=datetime.now(settings.tz), enabled=True)
        # Never print note text, PDFs, request URLs or provider exceptions.
        counts = {}
        for outcome in result["outcomes"].values():
            counts[outcome] = counts.get(outcome, 0) + 1
        print(json.dumps({"processed": result["processed"], "statuses": counts}))
        if not args.watch:
            return 0
        await asyncio.sleep(30)


def main():
    args = parser().parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("summary_companion=failed; inspect configuration without exposing secrets")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
