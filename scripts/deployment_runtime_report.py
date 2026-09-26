"""Read-only, allowlisted runtime report; never dump the process environment."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
from importlib.metadata import version
from pathlib import Path

MODULES = (
    "extraction.adapters", "backend.intake", "backend.conversations",
    "backend.decision_intents", "backend.summaries", "backend.investigation",
    "rag.clients", "rag.explanation", "model_json",
)


def report() -> dict:
    configs = {}
    classes = {
        "quote": ("extraction.adapters", "OpenAICompatibleConfig"),
        "requirement": ("backend.intake", "RequirementModelConfig"),
        "conversation": ("backend.conversations", "ConversationModelConfig"),
        "intent": ("backend.decision_intents", "DecisionIntentModelConfig"),
        "summary": ("backend.summaries", "SummaryModelConfig"),
        "investigation": ("backend.investigation", "AgentConfig"),
        "explanation": ("rag.explanation", "ExplanationConfig"),
        "embedding": ("rag.clients", "EmbeddingConfig"),
        "rerank": ("rag.clients", "RerankConfig"),
    }
    for name, (module, cls) in classes.items():
        try:
            config = getattr(importlib.import_module("supplier_comparison." + module), cls).from_env()
            if config is None:
                configs[name] = {"configured": False}
                continue
            item = {key: getattr(config, key) for key in (
                "provider", "model_id", "environment", "timeout_seconds", "max_attempts", "max_tokens",
            ) if hasattr(config, key)}
            # Endpoint may contain credentials in malformed config: report host/path only.
            from urllib.parse import urlsplit
            url = urlsplit(config.base_url)
            item["endpoint"] = {"scheme": url.scheme, "host": url.hostname, "path": url.path}
            item["credential_env_name"] = config.api_key_env
            item["credential_configured"] = bool(os.getenv(config.api_key_env or ""))
            configs[name] = item
        except Exception as exc:
            # Validation exception strings may include secret input values.
            configs[name] = {"config_error_type": type(exc).__name__}
    hashes = {}
    for module in MODULES:
        spec = importlib.util.find_spec("supplier_comparison." + module)
        if spec and spec.origin:
            hashes[module] = hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()
    packages = {name: version(name) for name in ("pydantic", "fastapi", "langgraph", "pdfplumber")}
    return {"python": platform.python_version(), "packages": packages,
            "effective_configs": configs, "source_sha256": hashes}


if __name__ == "__main__":
    print(json.dumps(report(), indent=2, default=str))
