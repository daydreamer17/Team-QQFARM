from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://supplier_app:supplier_dev_password@localhost:5432/supplier_comparison"
    quote_storage_path: Path = Path(".local-data/quotes")
    policy_upload_storage_path: Path = Path(".local-data/policy-uploads")
    policy_upload_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    policy_upload_max_pdf_pages: int = Field(default=50, ge=1)
    policy_upload_max_extracted_characters: int = Field(default=200_000, ge=1)
    quote_dictionary_path: Path = Path("data/contracts/quote_data_field.csv")
    test_user_id: str = "local-test-user"
    supplier_model_provider: str | None = None
    supplier_model_model_id: str | None = None
    supplier_model_environment: str = "LOCAL"
    supplier_prompt_version: str = "quote-extraction/1.0.0"
    allow_legacy_direct_quote_upload: bool = False
    supplier_agent_enabled: bool = False
    supplier_agent_policy_max_retries: int = Field(default=2, ge=0, le=3)
    supplier_conversation_job_stale_seconds: int = Field(default=120, ge=30, le=3600)
    supplier_worker_heartbeat_interval_seconds: float = Field(default=5, gt=0, le=60)
    supplier_worker_heartbeat_stale_seconds: float = Field(default=20, gt=1, le=300)
    supplier_history_root: Path = Path("data/generated/supplier_history/mcu9")
    supplier_history_dataset_version: str = "2026-08-06-v1"


settings = BackendSettings()
