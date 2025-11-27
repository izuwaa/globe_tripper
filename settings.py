"""Application settings loaded from environment or .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from globe_tripper.utils.llm_client import LLMConfig


class AppSettings(BaseSettings):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: int = 512
    openai_api_key: str | None = None
    google_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GLOBE_", extra="ignore")

    def llm_config(self) -> LLMConfig:
        return LLMConfig(
            provider=self.provider,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
