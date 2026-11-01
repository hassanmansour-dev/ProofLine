"""Settings from environment (.env). Snapshotted into RunConfig per run (ADR-0004)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from proofline.schemas import ModelSpec, RunConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="PROOFLINE_", extra="ignore", frozen=True
    )

    env: str = "dev"
    checkpointer_url: str = "sqlite:///.data/checkpoints.db"
    output_dir: Path = Path("outputs")
    brand_voice_path: Path = Path("docs/BRAND_VOICE.md")
    rubric_path: Path = Path("docs/RUBRIC.md")

    score_threshold: float = 8.0
    max_revisions: int = 3
    max_sources: int = 8
    results_per_query: int = 5
    max_source_chars: int = 4000
    search_concurrency: int = Field(default=4, ge=1)
    recursion_limit: int = 40

    planner_model: str = "claude-haiku-5-5"
    researcher_model: str = "claude-haiku-5-5"
    writer_model: str = "claude-sonnet-5-5"
    critic_model: str = "claude-sonnet-5-5"

    def run_config(self) -> RunConfig:
        return RunConfig(
            score_threshold=self.score_threshold,
            max_revisions=self.max_revisions,
            max_sources=self.max_sources,
            results_per_query=self.results_per_query,
            max_source_chars=self.max_source_chars,
            models={
                "planner": ModelSpec(model=self.planner_model, temperature=0.3, max_tokens=2048),
                "researcher": ModelSpec(
                    model=self.researcher_model, temperature=0.0, max_tokens=4096
                ),
                "writer": ModelSpec(model=self.writer_model, temperature=0.7, max_tokens=8192),
                "critic": ModelSpec(model=self.critic_model, temperature=0.0, max_tokens=4096),
            },
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
