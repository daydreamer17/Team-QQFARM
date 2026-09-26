"""Generate a compact, reproducible judging-readiness report.

The report distinguishes deterministic offline acceptance from live-model
performance and keeps temporary JUnit files outside the repository.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    (
        "agent_grounding",
        (
            "tests/backend/test_agent_investigation_dataset.py",
            "tests/backend/test_investigation.py",
            "tests/backend/test_conversation_routing.py",
        ),
        (),
    ),
    (
        "policy_rag_and_rules",
        (
            "tests/rag/test_evaluation.py",
            "tests/rag/test_orchestration.py",
            "tests/rules/test_compliance.py",
        ),
        (),
    ),
    (
        "prompt_injection",
        (
            "tests/extraction/test_pdf_ocr.py",
            "tests/extraction/test_v4_boundaries.py",
            "tests/rag/test_orchestration.py",
        ),
        (
            "-k",
            "prompt_injection or untrusted_citation or "
            "errors_and_untrusted_citations_fail_closed",
        ),
    ),
)

SUITE_EXPLANATIONS = {
    "agent_grounding": {
        "meaning": "Agent answers remain bound to frozen task facts, valid citations, and permitted investigation steps.",
        "why_selected": "A fluent but unsupported procurement recommendation can create direct commercial and audit risk.",
        "method": "Offline fixtures exercise routing, tool planning, scope/version checks, citation validation, deterministic scenarios, and bilingual answer behavior.",
        "pass_criterion": "Every executed case passes; paid live-model cases may be reported separately as skipped.",
    },
    "policy_rag_and_rules": {
        "meaning": "Policy retrieval and deterministic rules preserve evidence gaps, conflicts, thresholds, and review-required outcomes.",
        "why_selected": "A retrieved clause is not itself proof of compliance, so RAG must not silently turn missing evidence into approval.",
        "method": "Reviewed policy fixtures test retrieval boundaries, amount thresholds, rule orchestration, conflicts, missing evidence, and fail-closed behavior.",
        "pass_criterion": "Expected clauses and rule outcomes match, and unresolved evidence is never promoted to PASS.",
    },
    "prompt_injection": {
        "meaning": "Instructions embedded in untrusted documents cannot become authority, forge evidence, or make retrieval fail open.",
        "why_selected": "Quotes, OCR text, and policy sources are controlled outside the Agent and are realistic indirect-prompt-injection surfaces.",
        "method": "English, Chinese, role-forgery, tool-request, fake-citation, and structured payloads remain quoted data; forged/cross-supplier citations are rejected; RAG tamper and error modes require review.",
        "pass_criterion": "All payload variants remain untrusted and every forged, tampered, conflicting, or unavailable source fails closed.",
    },
}


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        capture_output=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _git_worktree_dirty() -> bool | None:
    completed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True,
        capture_output=True, check=False,
    )
    return bool(completed.stdout.strip()) if completed.returncode == 0 else None


def _junit_counts(path: Path) -> dict[str, int | float]:
    root = ElementTree.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts: dict[str, int | float] = {
        "tests": 0, "failures": 0, "errors": 0, "skipped": 0, "seconds": 0.0,
    }
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            counts[key] = int(counts[key]) + int(suite.attrib.get(key, 0))
        counts["seconds"] = float(counts["seconds"]) + float(suite.attrib.get("time", 0))
    counts["passed"] = (
        int(counts["tests"]) - int(counts["failures"])
        - int(counts["errors"]) - int(counts["skipped"])
    )
    return counts


def _markdown(report: dict) -> str:
    totals = report["totals"]
    lines = [
        "# QuoteWise Judging Readiness Report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Commit: `{report['git_commit']}`",
        f"- Working tree dirty: `{report['git_worktree_dirty']}`",
        f"- Python: `{report['python_version']}`",
        f"- Overall status: **{report['status']}**",
        f"- Automated acceptance rate: **{report['automated_acceptance_rate_percent']:.2f}%**",
        "",
        "| Suite | Passed | Failed | Errors | Skipped | Seconds |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for suite in report["suites"]:
        lines.append(
            f"| {suite['name']} | {suite['passed']} | {suite['failures']} | "
            f"{suite['errors']} | {suite['skipped']} | {suite['seconds']:.2f} |"
        )
    lines.extend([
        f"| **Total** | **{totals['passed']}** | **{totals['failures']}** | "
        f"**{totals['errors']}** | **{totals['skipped']}** | **{totals['seconds']:.2f}** |",
        "",
        "## Interpretation",
        "",
        "These are deterministic offline acceptance results for Agent routing, grounding, "
        "policy/RAG boundaries, rules, and prompt-injection handling. They do not measure "
        "live-model latency, token cost, user time saved, or production authentication.",
        "",
        "## What each suite measures",
        "",
    ])
    for suite in report["suites"]:
        lines.extend([
            f"### {suite['name']}",
            "",
            f"- Meaning: {suite['meaning']}",
            f"- Why selected: {suite['why_selected']}",
            f"- Test method: {suite['method']}",
            f"- Pass criterion: {suite['pass_criterion']}",
            "",
        ])
    lines.extend([
        "## Metric definition",
        "",
        "`Automated acceptance rate = passed / (passed + failed + errors)`. "
        "Skipped cases are disclosed but excluded from the denominator. This is a deterministic "
        "test acceptance rate, not a claim that model answers are 100% accurate in production.",
        "",
    ])
    if report["status"] != "PASS":
        lines.extend(["## Failure diagnostics", ""])
        for suite in report["suites"]:
            if suite["return_code"] != 0:
                lines.extend([
                    f"### {suite['name']}", "", "```text",
                    suite["diagnostic"], "```", "",
                ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    generated_at = datetime.now(timezone.utc)
    output_dir = args.output_dir or (
        ROOT / "evaluation/results/local/judging-readiness"
        / generated_at.strftime("%Y%m%dT%H%M%SZ")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    suite_reports: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="quotewise-judging-") as temporary:
        temp_dir = Path(temporary)
        for name, paths, extra in SUITES:
            junit = temp_dir / f"{name}.xml"
            command = [
                sys.executable, "-m", "pytest", *paths, *extra,
                "-q", f"--junitxml={junit}",
            ]
            completed = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True, check=False,
            )
            counts = _junit_counts(junit) if junit.exists() else {
                "tests": 0, "passed": 0, "failures": 0, "errors": 1,
                "skipped": 0, "seconds": 0.0,
            }
            suite_reports.append({
                "name": name,
                "command": command,
                "return_code": completed.returncode,
                **SUITE_EXPLANATIONS[name],
                **counts,
                "diagnostic": "\n".join(
                    (completed.stdout + "\n" + completed.stderr).strip().splitlines()[-40:]
                ),
            })

    totals = {
        key: sum(
            float(suite[key]) if key == "seconds" else int(suite[key])
            for suite in suite_reports
        )
        for key in ("tests", "passed", "failures", "errors", "skipped", "seconds")
    }
    assessed = int(totals["passed"]) + int(totals["failures"]) + int(totals["errors"])
    acceptance_rate = int(totals["passed"]) / assessed * 100 if assessed else 0.0
    status = "PASS" if all(suite["return_code"] == 0 for suite in suite_reports) else "FAIL"
    report = {
        "schema_version": "judging-readiness/1.1.0",
        "generated_at": generated_at.isoformat(),
        "git_commit": _git_commit(),
        "git_worktree_dirty": _git_worktree_dirty(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "status": status,
        "automated_acceptance_rate_percent": acceptance_rate,
        "totals": totals,
        "suites": suite_reports,
        "not_measured": [
            "live-model latency", "token cost", "real-user time saved",
            "production authentication and tenant isolation",
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
