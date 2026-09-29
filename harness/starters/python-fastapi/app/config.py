from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "app"
    app_env: str = "dev"  # dev | staging | production
    data_dir: Path = Path("./data")
    log_level: str = "INFO"


settings = Settings()
