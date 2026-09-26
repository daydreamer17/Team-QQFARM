"""Production/local config parity without contacting Docker daemon or providers."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from supplier_comparison.backend.conversations import ConversationModelConfig
from supplier_comparison.backend.decision_intents import DecisionIntentModelConfig
from supplier_comparison.rag.explanation import ExplanationConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("factory,prefix", [
    (ConversationModelConfig, "SUPPLIER_CONVERSATION_MODEL_"),
    (DecisionIntentModelConfig, "SUPPLIER_DECISION_INTENT_MODEL_"),
    (ExplanationConfig, "SUPPLIER_EXPLANATION_"),
])
def test_blank_optional_config_inherits_main_model(monkeypatch, factory, prefix):
    for name in ("MODEL_ID", "BASE_URL", "API_KEY_ENV", "PROVIDER"):
        monkeypatch.setenv(prefix + name, "")
    monkeypatch.setenv("SUPPLIER_MODEL_MODEL_ID", "organizer-model")
    monkeypatch.setenv("SUPPLIER_MODEL_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("SUPPLIER_MODEL_API_KEY_ENV", "LLM_GATEWAY_API_KEY")
    monkeypatch.setenv("SUPPLIER_MODEL_PROVIDER", "openai-compatible")
    config = factory.from_env()
    assert config.model_id == "organizer-model"
    assert config.base_url == "https://gateway.example/v1"
    assert config.api_key_env == "LLM_GATEWAY_API_KEY"
    if hasattr(config, "provider"):
        assert config.provider == "openai-compatible"


@pytest.mark.parametrize("production", [False, True])
def test_compose_forwards_runtime_overrides_to_both_processes(tmp_path, production):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is required; daemon is not needed")
    overrides = {
        "POSTGRES_PASSWORD": "dummy-test-only", "LLM_GATEWAY_API_KEY": "dummy-test-only",
        "SUPPLIER_MODEL_MODEL_ID": "organizer-model",
        "SUPPLIER_MODEL_BASE_URL": "https://gateway.example/v1",
        "SUPPLIER_MODEL_API_KEY_ENV": "LLM_GATEWAY_API_KEY",
        "SUPPLIER_MODEL_TIMEOUT_SECONDS": "177", "SUPPLIER_MODEL_MAX_TOKENS": "7000",
        "SUPPLIER_MODEL_RETRY_BACKOFF_SECONDS": "0.7",
        "SUPPLIER_REQUIREMENT_MODEL_MODEL_ID": "requirement-model",
        "SUPPLIER_REQUIREMENT_MODEL_TIMEOUT_SECONDS": "171",
        "SUPPLIER_REQUIREMENT_MODEL_MAX_TOKENS": "5100",
        "SUPPLIER_SUMMARY_MODEL_MODEL_ID": "summary-model",
        "SUPPLIER_SUMMARY_MODEL_TIMEOUT_SECONDS": "172",
        "SUPPLIER_EXPLANATION_MODEL_ID": "explanation-model",
        "SUPPLIER_EXPLANATION_TIMEOUT_SECONDS": "173",
        "SUPPLIER_AGENT_ENABLED": "true", "SUPPLIER_AGENT_MAX_MODEL_CALLS": "4",
        "SUPPLIER_AGENT_MAX_SECONDS": "200",
        "SUPPLIER_WORKER_HEARTBEAT_STALE_SECONDS": "42",
        "SUPPLIER_CONVERSATION_JOB_STALE_SECONDS": "600",
    }
    env_file = tmp_path / "config.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in overrides.items()))
    args = ["docker", "compose", "--env-file", str(env_file), "-f", "compose.yaml"]
    if production:
        args += ["-f", "compose.prod.yaml"]
    args += ["config", "--format", "json"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SUPPLIER_", "POSTGRES_", "COMPOSE_", "LLM_", "QQFARM_"))}
    result = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, check=True)
    services = json.loads(result.stdout)["services"]
    for service in ("api", "worker"):
        actual = services[service]["environment"]
        for key, expected in overrides.items():
            if key.startswith("SUPPLIER_"):
                assert actual[key] == expected, (service, key)
    assert services["api"]["environment"]["SUPPLIER_DECISION_INTENT_MODEL_API_KEY_ENV"] == "LLM_GATEWAY_API_KEY"
    assert services["worker"]["environment"]["SUPPLIER_CONVERSATION_MODEL_API_KEY_ENV"] == "LLM_GATEWAY_API_KEY"


def test_runtime_report_never_prints_credential_values(monkeypatch):
    from scripts.deployment_runtime_report import report
    monkeypatch.setenv("SUPPLIER_MODEL_API_KEY_ENV", "TEST_SECRET")
    monkeypatch.setenv("TEST_SECRET", "never-echo-this-secret")
    monkeypatch.setenv("SUPPLIER_MODEL_MODEL_ID", "test-model")
    monkeypatch.setenv("SUPPLIER_MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("SUPPLIER_MODEL_ENVIRONMENT", "ORGANIZER")
    monkeypatch.setenv("SUPPLIER_MODEL_BASE_URL", "https://gateway.example/v1")
    result = report()
    assert result["effective_configs"]["quote"]["credential_configured"] is True
    assert "never-echo-this-secret" not in json.dumps(result, default=str)
    assert len(result["source_sha256"]) == 9


def test_diagnostic_wrapper_falls_back_to_sudo_without_restarting(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands"
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\nexit 1\n')
    docker.chmod(0o755)
    sudo = fake_bin / "sudo"
    sudo.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DIAGNOSTIC_TEST_LOG"\nexit 0\n')
    sudo.chmod(0o755)
    env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
               DIAGNOSTIC_TEST_LOG=str(log))
    subprocess.run(["bash", "deploy/lightsail-diagnose.sh"], cwd=ROOT, env=env,
                   check=True, text=True, capture_output=True)
    commands = log.read_text().splitlines()
    assert commands[0] == "docker info"
    requests = [line for line in commands if " run --rm " in line]
    assert len(requests) == 1
    assert "--no-deps" in requests[0] and "PYTHONPATH=/repo/src" in requests[0]
    assert not any(" restart " in line or " up " in line or " build " in line for line in commands)
