#!/usr/bin/env python3
"""Prove that a cached OCR candidate can start and infer with sockets denied."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path

from supplier_comparison.ocr.engine import PaddleStructureV3Engine, TesseractEngine


REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("tesseract", "pp_structure_v3"), required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--tesseract-binary",
        type=Path,
        default=REPO_ROOT / ".ocr-tools/tesseract/bin/tesseract",
    )
    parser.add_argument(
        "--tessdata-dir",
        type=Path,
        default=REPO_ROOT / ".ocr-tools/tesseract/share/tessdata",
    )
    args = parser.parse_args()
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(REPO_ROOT / ".ocr-cache/paddlex"))
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

    network_attempts = []
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def denied_connect(self: socket.socket, address: object) -> None:
        network_attempts.append(str(address))
        raise RuntimeError("network denied by offline OCR probe")

    def denied_getaddrinfo(*args: object, **kwargs: object) -> None:
        network_attempts.append(str(args[0] if args else "unknown"))
        raise RuntimeError("DNS denied by offline OCR probe")

    socket.socket.connect = denied_connect
    socket.getaddrinfo = denied_getaddrinfo
    try:
        if args.engine == "tesseract":
            engine = TesseractEngine(
                args.tesseract_binary,
                tessdata_dir=args.tessdata_dir,
            )
        else:
            engine = PaddleStructureV3Engine(device="cpu")
        engine.start()
        result = engine.recognize(args.image, timeout_seconds=30.0)
        payload = {
            "result_kind": "V7_STAGE3_OCR_OFFLINE_STARTUP",
            "status": "PASSED",
            "engine": args.engine,
            "engine_version": engine.version(),
            "network_attempt_count": len(network_attempts),
            "text_region_count": len(result.text_regions),
            "image_sha256": result.image_sha256,
        }
    except Exception as exc:
        payload = {
            "result_kind": "V7_STAGE3_OCR_OFFLINE_STARTUP",
            "status": "FAILED",
            "engine": args.engine,
            "network_attempt_count": len(network_attempts),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finally:
        socket.socket.connect = original_connect
        socket.getaddrinfo = original_getaddrinfo
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
