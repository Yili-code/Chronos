"""Run the real Web UI with temporary synthetic tasks and no external services."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    # This is a developer demo, not a natural-language parsing demonstration.
    # Override settings before importing the app, so an existing .env cannot
    # register a webhook, open Firestore or enable a scheduler for this run.
    from chronos import settings as settings_module
    from chronos.settings import Settings
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="chronos-demo-") as directory:
        config = Settings(
            _env_file=None,
            database_backend="sqlite",
            database_path=Path(directory) / "demo.db",
            telegram_bot_token="",
            telegram_chat_id=None,
            telegram_webhook_secret="",
            public_base_url="",
            scheduler_secret="",
            enable_internal_scheduler=False,
            enable_study_tracking=False,
            gemini_api_key="",
            web_password="",
            timezone="Asia/Taipei",
        )
        settings_module.settings = config
        from chronos import main as app_module

        app_module.db.initialize()
        due = (datetime.now(config.tz) + timedelta(days=1)).replace(
            hour=17, minute=0, second=0, microsecond=0
        )
        app_module.tasks.create("Finish the report", due, "Chronos")
        app_module.tasks.create("Read the setup guide", None, None)
        print(f"Synthetic local demo: http://127.0.0.1:{args.port}")
        print("Temporary sample tasks; completion works. Add needs Gemini and is disabled here.")
        print("No Telegram, cloud database or scheduler. Ctrl+C stops the demo.")
        uvicorn.run(app_module.app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
