from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="CHRONOS_",
        env_ignore_empty=True, extra="ignore",
    )

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_chat_id: int | None = None
    public_base_url: str = ""
    scheduler_secret: str = ""
    enable_internal_scheduler: bool = True
    enable_study_tracking: bool = False
    enable_gmail: bool = False
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_account: str = ""
    gmail_keep_senders: str = ""  # Comma-separated exact addresses or @domains.
    gmail_max_messages: int = Field(default=100, ge=1, le=500)
    database_path: Path = Path("chronos.db")
    database_backend: Literal["sqlite", "firestore"] = "sqlite"
    firestore_project_id: str = ""
    firestore_database: str = "(default)"
    firestore_collection_prefix: str = "chronos"
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    study_gemini_model: str = "gemini-3.1-flash-lite"
    study_daily_request_limit: int = Field(default=0, ge=0)
    study_daily_token_limit: int = Field(default=0, ge=0)
    ai_timeout: float = Field(default=30, gt=0)
    web_username: str = "chronos"
    web_password: str = ""
    timezone: str = Field(default="Asia/Taipei")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
