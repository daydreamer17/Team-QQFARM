from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from supplier_comparison.backend.database import create_session_factory
from supplier_comparison.backend.settings import settings

from .clients import (
    EmbeddingClient,
    EmbeddingConfig,
    RerankClient,
    RerankConfig,
    SiliconFlowEmbeddingClient,
    SiliconFlowRerankClient,
)
from .evaluation import EvaluationCase, evaluate_retrievals
from .importer import PolicyImporter
from .ranking import validate_rerank_indexes
from .repository import SQLPolicyRepository
from .retriever import HybridPolicyRetriever


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m supplier_comparison.rag")
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import-policies")
    import_parser.add_argument("--manifest", required=True)
    import_parser.add_argument("--publish", action="store_true")
    subparsers.add_parser("smoke-models")
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--dataset", required=True)
    evaluate_parser.add_argument("--policy-set-version", required=True)
    evaluate_parser.add_argument("--policy-index-version", required=True)
    evaluate_parser.add_argument("--output", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    importer_factory: Callable[[], Any] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "import-policies":
        _validate_manifest_argument(args.manifest)
        importer = importer_factory() if importer_factory else _build_importer()
        outcome = importer.import_manifest(args.manifest, publish=args.publish)
        print(json.dumps(asdict(outcome), sort_keys=True))
        return 0
    embedding, rerank = _build_clients()
    if args.command == "smoke-models":
        print(json.dumps(smoke_models(embedding, rerank), sort_keys=True))
        return 0
    if args.command == "evaluate":
        dataset_path = Path(args.dataset).resolve()
        cases = [
            EvaluationCase.model_validate(item)
            for item in json.loads(dataset_path.read_text(encoding="utf-8"))
        ]
        engine, sessions = create_session_factory(settings.database_url)
        try:
            report = evaluate_retrievals(
                cases,
                retriever=HybridPolicyRetriever(SQLPolicyRepository(sessions), embedding, rerank),
                policy_set_version=args.policy_set_version,
                policy_index_version=args.policy_index_version,
            )
        finally:
            engine.dispose()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        print(json.dumps({"output": str(output), "rerank_recall_at_3": report.rerank_recall_at_3}))
        return 0
    raise AssertionError("unreachable")


def smoke_models(
    embedding: EmbeddingClient, rerank: RerankClient
) -> dict[str, Any]:
    phrase = "procurement policy retrieval smoke test"
    embedded = embedding.embed([phrase])
    if len(embedded.vectors) != 1 or len(embedded.vectors[0]) != embedding.dimension:
        raise ValueError("embedding smoke returned the wrong dimension")
    reranked = rerank.rerank(
        "Which policy mentions shipping?",
        ["The quote includes shipping charges.", "The quote includes warranty terms."],
        top_n=2,
    )
    indexes = validate_rerank_indexes(
        [item.index for item in reranked.items], candidate_count=2
    )
    if len(indexes) != 2:
        raise ValueError("rerank smoke did not return top_n results")
    return {
        "status": "OK",
        "embedding_model": embedding.model_id,
        "embedding_dimension": len(embedded.vectors[0]),
        "embedding_attempts": embedded.attempts,
        "rerank_model": rerank.model_id,
        "rerank_indexes": indexes,
        "rerank_attempts": reranked.attempts,
    }


def _build_clients() -> tuple[SiliconFlowEmbeddingClient, SiliconFlowRerankClient]:
    load_dotenv(Path.cwd() / ".env", override=False)
    return (
        SiliconFlowEmbeddingClient(EmbeddingConfig.from_env()),
        SiliconFlowRerankClient(RerankConfig.from_env()),
    )


def _build_importer() -> PolicyImporter:
    engine, sessions = create_session_factory(settings.database_url)
    del engine  # The session factory owns connections for this short-lived CLI process.
    embedding, _rerank = _build_clients()
    return PolicyImporter(
        sessions,
        embedding,
        allowed_root=_policy_root(),
        provider=embedding.config.provider,
    )


def _policy_root() -> Path:
    configured = Path(os.getenv("SUPPLIER_POLICY_ROOT", "data/policies"))
    return (configured if configured.is_absolute() else Path.cwd() / configured).resolve()


def _validate_manifest_argument(value: str) -> None:
    path = Path(value)
    if path.is_absolute():
        raise ValueError("manifest path must be relative to the project policy directory")
    if ".." in path.parts:
        raise ValueError("manifest path cannot contain directory traversal")
