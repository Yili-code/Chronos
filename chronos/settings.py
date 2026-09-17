from pathlib import Path
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
    projects_root: Path = Path.home() / "Documents" / "Developing"
    database_path: Path = Path("chronos.db")
    github_token: str = ""
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout: float = Field(default=30, gt=0)
    web_username: str = "chronos"
    web_password: str = ""
    timezone: str = Field(default="Asia/Taipei")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
