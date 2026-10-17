from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "TaskFlow API"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "development-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080

    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "taskflow"
    POSTGRES_PASSWORD: str = "taskflow_password"
    POSTGRES_DB: str = "taskflow_db"
    DATABASE_URL: str = (
        "postgresql+asyncpg://taskflow:taskflow_password@localhost:5432/taskflow_db"
    )

    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    SESSION_COOKIE_NAME: str = "taskflow_session"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    CSRF_COOKIE_NAME: str = "taskflow_csrf"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"

    ENABLE_PUBLIC_REGISTRATION: bool = True
    MAX_BULK_UPDATE_LIMIT: int = 50

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
