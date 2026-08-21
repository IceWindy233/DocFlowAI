from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]
# 本地开发允许从被 Git 忽略的 .env 注入密钥；已有 shell 环境变量优先且不会被覆盖。
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_prefix="DOCFLOW_",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = "development"
    app_name: str = "DocFlow AI"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///../data/docflow.db"
    redis_url: str = "redis://127.0.0.1:6379/0"
    qdrant_url: str = "http://127.0.0.1:6333"
    artifact_root: Path = Path("../data/artifacts")
    report_root: Path = Path("../data/reports")
    source_root: Path = Path("..")
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    execution_mode: str = "inline"
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                parsed = json.loads(value)
                if not isinstance(parsed, list):
                    raise ValueError("CORS origins 的 JSON 必须是数组")
                return parsed
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def ensure_directories(self) -> None:
        self.artifact_root.expanduser().resolve().mkdir(parents=True, exist_ok=True)
        self.report_root.expanduser().resolve().mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
