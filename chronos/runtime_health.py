"""Fail-closed production configuration and side-effect-free readiness checks."""

from dataclasses import dataclass


class RuntimeConfigurationError(RuntimeError):
    pass


PUBLIC_REQUIRED_SETTINGS = {
    "telegram_bot_token": "Telegram bot token",
    "telegram_webhook_secret": "Telegram webhook secret",
    "telegram_chat_id": "Telegram owner chat",
    "scheduler_secret": "scheduler secret",
    "web_password": "Web password",
}


def public_mode(settings) -> bool:
    return settings.database_backend == "firestore" or bool(settings.public_base_url.strip())


def missing_public_settings(settings) -> list[str]:
    return [label for field, label in PUBLIC_REQUIRED_SETTINGS.items() if not getattr(settings, field)]


def validate_runtime_security(settings) -> None:
    if not public_mode(settings):
        return
    missing = missing_public_settings(settings)
    if missing:
        raise RuntimeConfigurationError(
            "Public mode requires: " + ", ".join(missing) + "."
        )


def readiness_snapshot(db, settings) -> dict:
    validate_runtime_security(settings)
    db.check_ready()
    return {
        "status": "ready",
        "mode": "public" if public_mode(settings) else "local",
        "database": settings.database_backend,
        "release": settings.release_sha or "development",
    }


def configured_components(settings) -> dict:
    return {
        "database": settings.database_backend,
        "telegram": "configured" if settings.telegram_bot_token and settings.telegram_chat_id else "disabled",
        "web_auth": "configured" if settings.web_password else "local-only",
        "scheduler_auth": "configured" if settings.scheduler_secret else "disabled",
        "study": "configured" if settings.enable_study_tracking else "disabled",
        "gmail": "configured" if settings.enable_gmail else "disabled",
        "release": settings.release_sha or "development",
    }
