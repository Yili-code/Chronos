"""Run explicitly requested assignment preparation without LMS material access."""

import argparse
import asyncio
from datetime import datetime
import json


def parser():
    result = argparse.ArgumentParser(
        description="Process queued assignment-preparation requests from saved text"
    )
    result.add_argument("--enable", action="store_true")
    result.add_argument(
        "--check", action="store_true",
        help="Inspect local prerequisites only; no writes or network calls",
    )
    result.add_argument("--firestore-project")
    result.add_argument("--firestore-database")
    result.add_argument("--firestore-prefix")
    result.add_argument("--free-tier-confirmed", action="store_true")
    result.add_argument("--watch", action="store_true", help="Repeat until interrupted")
    return result


def routed_settings(config, args):
    route = (args.firestore_project, args.firestore_database, args.firestore_prefix)
    if not any(value is not None for value in route):
        return config
    if not all(isinstance(value, str) and value.strip() and "/" not in value for value in route):
        raise ValueError("explicit Firestore project, database and prefix are required together")
    return config.model_copy(update={
        "database_backend": "firestore",
        "firestore_project_id": route[0],
        "firestore_database": route[1],
        "firestore_collection_prefix": route[2],
    })


def preflight(config):
    return {
        "mode": "read_only_preflight",
        "database_backend": config.database_backend,
        "sqlite_file_exists": (
            config.database_path.is_file() if config.database_backend == "sqlite" else None
        ),
        "firestore_project_explicit": bool(config.firestore_project_id),
        "telegram_configured": bool(config.telegram_chat_id and config.telegram_bot_token),
        "provider_key_configured": bool(
            config.gemini_api_key or config.gemini_api_key_secondary
        ),
        "daily_budget_configured": (
            config.preparation_daily_request_limit > 0
            and config.preparation_daily_token_limit > 0
        ),
        "remote_readiness": "not_checked",
    }


async def run(args):
    from .settings import settings

    config = routed_settings(settings, args)
    if args.check:
        print(json.dumps(preflight(config)))
        return 0
    if not args.enable:
        print("preparation_companion=disabled")
        return 0
    if not args.free_tier_confirmed:
        print("preparation_companion=configuration_required")
        return 2
    if not config.telegram_chat_id or not config.telegram_bot_token:
        print("preparation_companion=configuration_required")
        return 2
    if not (config.gemini_api_key or config.gemini_api_key_secondary):
        print("preparation_companion=configuration_required")
        return 2

    from .ai_budget import DailyAIBudget, notify_budget
    from .db import create_database
    from .preparation_ai import GeminiPreparationProvider
    from .preparation_worker import notify_preparation_failures, run_preparation_pass
    from .telegram import TelegramClient

    db = create_database(config)
    db.initialize()
    bot = TelegramClient(config.telegram_bot_token)
    budget = DailyAIBudget(
        db,
        request_limit=config.preparation_daily_request_limit,
        token_limit=config.preparation_daily_token_limit,
    )

    async def budget_notice(status, now):
        await notify_budget(db, bot, config.telegram_chat_id, status, now)

    provider_config = config.model_copy(update={"gemini_model": config.preparation_gemini_model})
    provider = GeminiPreparationProvider(
        provider_config,
        free_tier_confirmed=True,
        budget=budget,
        budget_notice=budget_notice,
    )
    while True:
        now = datetime.now(config.tz)
        result = await run_preparation_pass(
            db, provider, bot, config.telegram_chat_id, now
        )
        await notify_preparation_failures(db, bot, config.telegram_chat_id, now)
        print(json.dumps(result))
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
        print("preparation_companion=failed; inspect configuration without exposing secrets")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
