"""Environment-driven application settings."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from environment variables (and an optional ``.env`` file)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    github_token: SecretStr | None = Field(
        default=None, description="GitHub personal access token."
    )
    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2022-11-28"
    github_request_timeout: float = Field(default=30.0, gt=0, le=300)
    github_max_retries: int = Field(default=3, ge=0, le=10)
    github_max_rate_limit_wait: float = Field(
        default=60.0,
        ge=0,
        le=3600,
        description="Longest rate-limit wait (s) we will sleep through.",
    )
    github_enable_write_tools: bool = False
    gitmcp_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    gitmcp_max_file_bytes: int = Field(default=200_000, ge=1, le=10_000_000)
    gitmcp_max_patch_chars: int = Field(default=8_000, ge=100)
    gitmcp_max_comment_chars: int = Field(default=4_000, ge=100)
    gitmcp_max_response_bytes: int = Field(default=5_000_000, ge=10_000)

    @field_validator("github_token", mode="before")
    @classmethod
    def _blank_token_is_none(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("gitmcp_log_level", mode="before")
    @classmethod
    def _upper(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    @field_validator("github_api_url")
    @classmethod
    def _api_url(cls, v: str) -> str:
        v = v.rstrip("/")
        if not v.startswith(("https://", "http://")):
            raise ValueError("GITHUB_API_URL must start with https:// or http://")
        return v

    @property
    def token_value(self) -> str | None:
        return self.github_token.get_secret_value() if self.github_token else None
