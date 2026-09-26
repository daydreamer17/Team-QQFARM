"""Run the paid, opt-in QuoteWise live-model benchmark.

This command exercises the real conversation and investigation path against an
isolated synthetic procurement task. It never runs unless --confirm-paid is
supplied, and it is intentionally excluded from normal CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from supplier_comparison.backend.conversations import ConversationModelConfig
from supplier_comparison.backend.investigation import AgentConfig


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/reference/live_model_benchmark_cases.json"
TEST_NODE = (
    "tests/backend/test_api.py::"
    "test_live_model_benchmark_records_grounded_end_to_end_metrics"
)


def _git_value(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=ROOT, text=True,
        capture_output=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _percentile(values: list[float], probability: float) -> float | None:
    """Linear-interpolated percentile over observed values."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _markdown(report: dict) -> str:
    metrics = report["metrics"]
    token = report["token_usage"]
    lines = [
        "# QuoteWise Live Model Evaluation",
        "",
        "> Paid, opt-in evaluation over synthetic procurement data. It is not part of normal CI.",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Commit: `{report['git_commit']}`",
        f"- Working tree dirty: `{report['git_worktree_dirty']}`",
        f"- Dataset: `{report['dataset_id']}` (`{report['dataset_sha256']}`)",
        f"- Provider/model: `{report['provider']}` / `{report['model_id']}`",
        f"- Repeats: `{report['repeats']}`",
        f"- Overall status: **{report['status']}**",
        "",
        "## Reliability and latency",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        f"| Successful cases | {metrics['passed_cases']} / {metrics['expected_cases']} |",
        f"| Success rate | {metrics['success_rate_percent']:.2f}% |",
        f"| Facts and citations validated | {metrics['facts_and_citations_passed']} / {metrics['expected_cases']} |",
        f"| Official state unchanged | {metrics['official_state_unchanged']} / {metrics['expected_cases']} |",
        f"| End-to-end latency P50 | {metrics['end_to_end_latency_ms']['p50']} ms |",
        f"| End-to-end latency P95 | {metrics['end_to_end_latency_ms']['p95']} ms |",
        f"| Provider-call latency P50 | {metrics['provider_latency_ms']['p50']} ms |",
        f"| Provider-call latency P95 | {metrics['provider_latency_ms']['p95']} ms |",
        "",
        "## Token usage",
        "",
        f"- Prompt tokens: `{token['prompt_tokens']}`",
        f"- Completion tokens: `{token['completion_tokens']}`",
        f"- Total tokens: `{token['total_tokens']}`",
        f"- Provider calls reporting usage: `{token['calls_with_usage']} / {token['provider_calls']}`",
    ]
    if report["estimated_cost"] is not None:
        if report["estimated_cost"]["amount"] is None:
            lines.append(
                "- Optional estimated cost: `unavailable` because not every provider call returned split input/output usage"
            )
        else:
            lines.append(
                f"- Optional estimated cost: `{report['estimated_cost']['amount']}` "
                f"`{report['estimated_cost']['currency']}`"
            )
        lines.extend([
            "",
            "The estimate uses operator-supplied prices; QuoteWise does not hard-code provider pricing.",
        ])
    lines.extend([
        "",
        "## Per-case stability",
        "",
        "| Case | Passed / Runs | Success rate | P50 ms | P95 ms |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for row in report["case_stability"]:
        lines.append(
            f"| {row['case_id']} | {row['passed']} / {row['runs']} | "
            f"{row['success_rate_percent']:.2f}% | {row['p50_latency_ms']} | "
            f"{row['p95_latency_ms']} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "A case passes only when the real-model workflow completes, the required investigation tools or "
        "deterministic simulation preview are observed, the persisted response passes the application's "
        "grounding checks, and the official task state remains unchanged. P50/P95 use linear interpolation "
        "over this run's observations.",
        "",
        "This report measures the configured model in the current environment. A small synthetic benchmark is "
        "a regression signal, not a production SLA or a substitute for real-user evaluation.",
        "",
    ])
    if report["diagnostics"]:
        lines.extend(["## Failed-run diagnostics", "", "```text"])
        lines.extend(report["diagnostics"])
        lines.extend(["```", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-paid", action="store_true",
        help="Acknowledge that this run calls the configured paid model provider.",
    )
    parser.add_argument("--repeats", type=int, default=1, help="Runs per fixed case (1-10).")
    parser.add_argument(
        "--case", dest="case_ids", action="append", default=[],
        help="Run only this case ID; repeat the option to select multiple cases.",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--env-file", type=Path, default=ROOT / ".env",
        help="Environment file to load without overriding variables already set in the shell.",
    )
    parser.add_argument("--input-cost-per-million", type=Decimal)
    parser.add_argument("--output-cost-per-million", type=Decimal)
    parser.add_argument("--cost-currency", default="USD")
    args = parser.parse_args()
    if not args.confirm_paid:
        parser.error("--confirm-paid is required; this evaluation makes paid live-model calls")
    if not 1 <= args.repeats <= 10:
        parser.error("--repeats must be between 1 and 10")
    if (args.input_cost_per_million is None) != (args.output_cost_per_million is None):
        parser.error("provide both input and output token prices, or neither")
    if any(
        value is not None and value < 0
        for value in (args.input_cost_per_million, args.output_cost_per_million)
    ):
        parser.error("token prices must be non-negative")

    env_file = args.env_file.resolve()
    if not env_file.is_file():
        parser.error(f"environment file does not exist: {env_file}")
    load_dotenv(env_file, override=False)

    model_id = os.getenv("SUPPLIER_AGENT_MODEL_ID") or os.getenv("SUPPLIER_MODEL_MODEL_ID")
    if not model_id:
        parser.error(
            "set SUPPLIER_AGENT_MODEL_ID or SUPPLIER_MODEL_MODEL_ID in the shell or env file"
        )
    conversation = ConversationModelConfig.from_env()
    if conversation is None:
        parser.error(
            "the conversation model is incomplete; configure its model ID and base URL"
        )
    agent = AgentConfig.from_env()
    api_key = os.getenv(agent.api_key_env, "").strip()
    if not api_key or api_key.casefold() in {
        "replace-with-secret", "change-me", "changeme", "your-api-key",
    }:
        parser.error(
            f"Agent API key environment variable is missing or still a placeholder: {agent.api_key_env}"
        )

    dataset_bytes = DATASET.read_bytes().replace(b"\r\n", b"\n")
    dataset = json.loads(dataset_bytes)
    cases = dataset["cases"]
    available_case_ids = {case["id"] for case in cases}
    unknown_case_ids = sorted(set(args.case_ids) - available_case_ids)
    if unknown_case_ids:
        parser.error(
            "unknown --case value(s): " + ", ".join(unknown_case_ids)
        )
    if args.case_ids:
        selected = set(args.case_ids)
        cases = [case for case in cases if case["id"] in selected]
    generated_at = datetime.now(timezone.utc)
    output_dir = args.output_dir or (
        ROOT / "evaluation/results/local/live-model"
        / generated_at.strftime("%Y%m%dT%H%M%SZ")
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    records: list[dict] = []
    diagnostics: list[str] = []
    return_codes: list[int] = []
    with tempfile.TemporaryDirectory(prefix="quotewise-live-model-") as temporary:
        temporary_dir = Path(temporary)
        for run_index in range(1, args.repeats + 1):
            sink = temporary_dir / f"run-{run_index}.ndjson"
            environment = dict(os.environ)
            environment.update({
                "RUN_LIVE_MODEL_BENCHMARK": "1",
                "LIVE_MODEL_BENCHMARK_RECORDS_PATH": str(sink),
                "LIVE_MODEL_BENCHMARK_RUN_INDEX": str(run_index),
                "SUPPLIER_AGENT_ENABLED": "true",
            })
            test_nodes = (
                [f"{TEST_NODE}[{case['id']}]" for case in cases]
                if args.case_ids else [TEST_NODE]
            )
            command = [sys.executable, "-m", "pytest", *test_nodes, "-q", "--tb=short"]
            completed = subprocess.run(
                command, cwd=ROOT, env=environment, text=True,
                capture_output=True, check=False,
            )
            return_codes.append(completed.returncode)
            run_records = _load_records(sink)
            records.extend(run_records)
            expected_ids = {case["id"] for case in cases}
            observed_ids = {row.get("case_id") for row in run_records}
            for missing in sorted(expected_ids - observed_ids):
                records.append({
                    "case_id": missing,
                    "run_index": run_index,
                    "status": "FAILED",
                    "error_code": "benchmark_record_missing",
                    "facts_and_citations_valid": False,
                    "official_state_unchanged": False,
                    "provider_calls": [],
                    "end_to_end_latency_ms": 0.0,
                    "usage": {},
                })
            if completed.returncode != 0:
                bounded = (completed.stdout + "\n" + completed.stderr).strip().splitlines()[-60:]
                diagnostics.append(f"run {run_index}:\n" + "\n".join(bounded))

    expected_cases = len(cases) * args.repeats
    passed = sum(row.get("status") == "PASSED" for row in records)
    end_to_end = [
        float(row["end_to_end_latency_ms"])
        for row in records if row.get("end_to_end_latency_ms") is not None
    ]
    provider_calls = [
        call for row in records for call in row.get("provider_calls", [])
    ]
    provider_latency = [
        float(call["latency_ms"])
        for call in provider_calls if call.get("latency_ms") is not None
    ]
    token_usage = {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    for call in provider_calls:
        usage = call.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if type(prompt_tokens) is int:
            token_usage["prompt_tokens"] += prompt_tokens
        if type(completion_tokens) is int:
            token_usage["completion_tokens"] += completion_tokens
        if type(total_tokens) is int:
            token_usage["total_tokens"] += total_tokens
        elif type(prompt_tokens) is int and type(completion_tokens) is int:
            token_usage["total_tokens"] += prompt_tokens + completion_tokens
    token_usage.update(
        provider_calls=len(provider_calls),
        calls_with_usage=sum(bool(call.get("usage")) for call in provider_calls),
        calls_with_split_usage=sum(
            type((call.get("usage") or {}).get("prompt_tokens")) is int
            and type((call.get("usage") or {}).get("completion_tokens")) is int
            for call in provider_calls
        ),
    )
    case_stability = []
    for case in cases:
        rows = [row for row in records if row.get("case_id") == case["id"]]
        latencies = [float(row["end_to_end_latency_ms"]) for row in rows]
        case_passed = sum(row.get("status") == "PASSED" for row in rows)
        case_stability.append({
            "case_id": case["id"],
            "runs": len(rows),
            "passed": case_passed,
            "success_rate_percent": case_passed / len(rows) * 100 if rows else 0.0,
            "p50_latency_ms": _rounded(_percentile(latencies, 0.50)),
            "p95_latency_ms": _rounded(_percentile(latencies, 0.95)),
        })

    estimated_cost = None
    if args.input_cost_per_million is not None and args.output_cost_per_million is not None:
        complete_usage = (
            token_usage["provider_calls"] > 0
            and token_usage["calls_with_split_usage"] == token_usage["provider_calls"]
        )
        cost = None if not complete_usage else (
            Decimal(token_usage["prompt_tokens"]) * args.input_cost_per_million
            + Decimal(token_usage["completion_tokens"]) * args.output_cost_per_million
        ) / Decimal(1_000_000)
        estimated_cost = {
            "amount": str(cost.quantize(Decimal("0.000001"))) if cost is not None else None,
            "currency": args.cost_currency,
            "input_cost_per_million": str(args.input_cost_per_million),
            "output_cost_per_million": str(args.output_cost_per_million),
            "usage_complete": complete_usage,
        }

    status = "PASS" if (
        passed == expected_cases
        and len(records) == expected_cases
        and all(code == 0 for code in return_codes)
    ) else "FAIL"
    report = {
        "schema_version": "live-model-evaluation/1.0.0",
        "generated_at": generated_at.isoformat(),
        "git_commit": _git_value("rev-parse", "HEAD"),
        "git_worktree_dirty": bool(_git_value("status", "--porcelain").strip()),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "provider": conversation.provider,
        "model_id": agent.model_id,
        "repeats": args.repeats,
        "selected_case_ids": [case["id"] for case in cases],
        "status": status,
        "metrics": {
            "expected_cases": expected_cases,
            "passed_cases": passed,
            "failed_cases": expected_cases - passed,
            "success_rate_percent": passed / expected_cases * 100 if expected_cases else 0.0,
            "facts_and_citations_passed": sum(
                bool(row.get("facts_and_citations_valid")) for row in records
            ),
            "official_state_unchanged": sum(
                bool(row.get("official_state_unchanged")) for row in records
            ),
            "end_to_end_latency_ms": {
                "p50": _rounded(_percentile(end_to_end, 0.50)),
                "p95": _rounded(_percentile(end_to_end, 0.95)),
            },
            "provider_latency_ms": {
                "p50": _rounded(_percentile(provider_latency, 0.50)),
                "p95": _rounded(_percentile(provider_latency, 0.95)),
            },
        },
        "token_usage": token_usage,
        "estimated_cost": estimated_cost,
        "case_stability": case_stability,
        "records": records,
        "diagnostics": diagnostics,
        "not_measured": [
            "real-user procurement time saved",
            "production concurrency SLA",
            "production authentication or tenant isolation",
        ],
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    markdown = _markdown(report)
    (output_dir / "report.md").write_text(markdown, encoding="utf-8")
    print(output_dir)
    print(markdown)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
