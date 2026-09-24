from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run maintained Agent investigation acceptance checks.")
    parser.add_argument("--live", action="store_true", help="Also call the configured paid live Agent model.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/backend/test_agent_investigation_dataset.py",
        "tests/backend/test_investigation.py",
        "-q",
    ]
    env = dict(os.environ)
    if args.live:
        env["RUN_AGENT_LIVE_TESTS"] = "1"
    completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    report = {
        "schema_version": "agent-investigation-evaluation/1.0.0",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "live_model_enabled": args.live,
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    output = args.output or (
        ROOT / "evaluation/results/local/agent_investigation_demo"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") / "report.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
