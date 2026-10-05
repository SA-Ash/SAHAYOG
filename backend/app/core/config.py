from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./sahyog.db"
    jwt_secret: str
    sahyog_service_token: str
    jwt_minutes: int = 30
    elevated_minutes: int = 5
    cookie_secure: bool = False
    frontend_origin: str = "http://localhost:5173"
    sahyog_mode: str = "mock"
    sahyog_url: str = "http://localhost:8001"
    upload_dir: Path = Path("../data/uploads")
    seed_demo: bool = False
    demo_password: str | None = None

    @model_validator(mode="after")
    def safe_settings(self):
        if len(self.jwt_secret) < 32 or len(self.sahyog_service_token) < 24:
            raise ValueError("JWT_SECRET (32+ chars) and SAHYOG_SERVICE_TOKEN (24+ chars) required")
        if self.sahyog_mode not in {"mock", "real"}:
            raise ValueError("SAHYOG_MODE must be mock or real")
        if self.seed_demo and (not self.demo_password or len(self.demo_password) < 12):
            raise ValueError("Demo seeding requires DEMO_PASSWORD (12+ chars)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
