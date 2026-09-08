from functools import lru_cache
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """由環境變數載入的應用程式設定。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://linebot:linebot@localhost:5432/linebot"
    line_channel_secret: str = ""
    line_channel_access_token: str = ""
    log_level: str = "INFO"
    timezone: str = "Asia/Taipei"
    public_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://linebot.wufamily.dpdns.org")
    )
    max_request_bytes: int = 1_048_576
    max_amount: int = 999_999_999
    draft_ttl_minutes: int = 30

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"無效的時區：{self.timezone}") from error

        if self.app_env == "production":
            missing = [
                name
                for name, value in (
                    ("LINE_CHANNEL_SECRET", self.line_channel_secret),
                    ("LINE_CHANNEL_ACCESS_TOKEN", self.line_channel_access_token),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"正式環境缺少必要祕密：{', '.join(missing)}")
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("正式環境必須使用 PostgreSQL asyncpg 連線")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
