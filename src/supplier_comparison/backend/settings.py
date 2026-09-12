from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://supplier_app:supplier_dev_password@localhost:5432/supplier_comparison"
    quote_storage_path: Path = Path(".local-data/quotes")
    quote_dictionary_path: Path = Path("data/contracts/quote_data_field.csv")
    test_user_id: str = "local-test-user"
    supplier_model_provider: str | None = None
    supplier_model_model_id: str | None = None
    supplier_model_environment: str = "LOCAL"
    supplier_prompt_version: str = "quote-extraction/1.0.0"


settings = BackendSettings()
