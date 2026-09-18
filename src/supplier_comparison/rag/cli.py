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
from .orchestration import PlanningContext, PolicyOrchestrator, load_reviewed_catalog
from .orchestration import PolicyEvidenceBundle
from .explanation import ExplanationConfig, LiveExplanationClient, PolicyExplanationService
from .clients import ModelClientError


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
    orchestration_parser = subparsers.add_parser("retrieve-plan")
    orchestration_parser.add_argument("--context", required=True)
    orchestration_parser.add_argument("--manifest", required=True)
    orchestration_parser.add_argument("--output", required=True)
    explain_parser = subparsers.add_parser("explain-plan")
    explain_parser.add_argument("--bundle", required=True)
    explain_parser.add_argument("--facts", required=True)
    explain_parser.add_argument("--manifest", required=True)
    explain_parser.add_argument("--output", required=True)
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
    if args.command == "explain-plan":
        load_dotenv(Path.cwd() / ".env", override=False)
        _validate_manifest_argument(args.manifest)
        manifest = load_reviewed_catalog(args.manifest, allowed_root=_policy_root())
        bundle = PolicyEvidenceBundle.model_validate_json(Path(args.bundle).read_text(encoding="utf-8"))
        facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        if not isinstance(facts, dict):
            raise ValueError("confirmed facts must be an object")
        validate_explanation_bundle(bundle, manifest)
        context_facts = bundle.plan.context.model_dump(mode="json")
        for key in ("currency", "total_cost", "category", "tax_mode"):
            if key in facts and facts[key] != context_facts[key]:
                raise ValueError("confirmed facts do not match the frozen planning context")
        if "procurement_region" in facts and facts["procurement_region"] != context_facts["region"]:
            raise ValueError("confirmed procurement region does not match the frozen planning context")
        client = LiveExplanationClient(ExplanationConfig.from_env())
        service = PolicyExplanationService(client)
        explanations = []
        seen = set()
        failure = None
        for result in bundle.retrieval_results:
            fresh = [c for c in result.citations if c.clause_id not in seen]
            if not fresh:
                continue
            try:
                explanations.append(service.explain(result.model_copy(update={"citations": fresh}), confirmed_facts=facts))
                seen.update(c.clause_id for c in fresh)
            except ModelClientError as exc:
                failure = exc.error_code
                break
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        report = {"status": "ERROR" if failure else "OK", "error_code": failure,
                  "bundle_id": bundle.bundle_id, "task_revision": bundle.plan.context.task_revision,
                  "snapshot_id": bundle.plan.context.snapshot_id,
                  "policy_set_version": bundle.plan.context.policy_set_version,
                  "policy_index_version": bundle.plan.context.policy_index_version,
                  "explanations": [] if failure else [e.model_dump(mode="json") for e in explanations],
                  "model_calls": client.telemetry}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "output": str(output),
                          "error_code": failure, "model_calls": len(client.telemetry),
                          "explained_clauses": len(seen) if not failure else 0}))
        return 1 if failure else 0
    embedding, rerank = _build_clients()
    if args.command == "smoke-models":
        print(json.dumps(smoke_models(embedding, rerank), sort_keys=True))
        return 0
    if args.command == "retrieve-plan":
        _validate_manifest_argument(args.manifest)
        manifest = load_reviewed_catalog(args.manifest, allowed_root=_policy_root())
        context = PlanningContext.model_validate_json(Path(args.context).read_text(encoding="utf-8"))
        engine, sessions = create_session_factory(settings.database_url)
        try:
            bundle = PolicyOrchestrator(manifest, HybridPolicyRetriever(
                SQLPolicyRepository(sessions), embedding, rerank)).retrieve(context)
        finally:
            engine.dispose()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        print(json.dumps({"output": str(output), "status": bundle.status,
                          "requests_used": bundle.requests_used,
                          "supported_requirements": sum(c.status == "SUPPORTED" for c in bundle.requirement_coverage),
                          "total_requirements": len(bundle.requirement_coverage)}))
        return 0 if bundle.status == "READY" else 1
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


def validate_explanation_bundle(bundle, manifest):
    """Recheck persisted input against the pinned authoritative catalog before calls."""
    expected = PolicyOrchestrator(manifest, None).plan(bundle.plan.context)
    if (bundle.status != "READY" or expected.status != "READY"
        or bundle.plan.manifest_sha256 != expected.manifest_sha256
        or bundle.plan.requirements != expected.requirements):
        raise ValueError("bundle is not current evidence-complete policy input")
    allowed = {r.clause_id: r for r in expected.requirements}
    covered = set()
    for result in bundle.retrieval_results:
        if (result.status.value != "OK" or result.missing_control_codes
            or result.policy_set_version != expected.context.policy_set_version
            or result.policy_index_version != expected.context.policy_index_version):
            raise ValueError("bundle contains failed or wrong-version retrievals")
        for citation in result.citations:
            req = allowed.get(citation.clause_id)
            if req is None or any(getattr(req, k) != getattr(citation, k) for k in
                ("policy_id", "document_id", "document_version", "text", "content_sha256", "control_code")):
                raise ValueError("bundle citation does not match authoritative policy")
            if citation.policy_set_version != result.policy_set_version:
                raise ValueError("bundle citation version mismatch")
            covered.add(citation.clause_id)
    if covered != set(allowed):
        raise ValueError("bundle does not cover all policy requirements")


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
