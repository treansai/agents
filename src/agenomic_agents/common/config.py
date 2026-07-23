from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables prefixed with APP_."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APP_",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["development", "test", "staging", "production"] = "development"
    api_key: SecretStr = SecretStr("development-only-key-change-me")
    ledger_hmac_key: SecretStr = SecretStr("development-only-ledger-key-change-me")
    log_level: str = "INFO"
    database_path: Path = Path("data/agents.db")
    adk_model: str = "gemini-2.5-flash"
    crew_model: str = "openai/gpt-5-mini"
    langgraph_model: str = "openai:gpt-5-mini"
    max_request_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    claims_human_review_threshold: float = Field(default=0.65, ge=0, le=1)
    devops_max_tool_calls: int = Field(default=12, ge=2, le=100)
    research_max_sources: int = Field(default=12, ge=1, le=50)

    @model_validator(mode="after")
    def reject_unsafe_production_defaults(self) -> "Settings":
        if self.env == "production":
            for name, value in {
                "APP_API_KEY": self.api_key.get_secret_value(),
                "APP_LEDGER_HMAC_KEY": self.ledger_hmac_key.get_secret_value(),
            }.items():
                if len(value) < 32 or "development-only" in value or "change-me" in value:
                    raise ValueError(f"{name} must be a strong secret in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
