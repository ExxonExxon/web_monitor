from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WM_", extra="ignore")

    app_name: str = "web-monitor"
    log_level: LogLevel = "INFO"
    timezone: str = "UTC"


def get_settings() -> Settings:
    return Settings()
