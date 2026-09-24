"""Read-only, result-bound tools for one-click decision investigation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationError

from supplier_comparison.rules.contracts import FrozenModel

from .investigation import InvestigationCase, ToolObservation, ToolResult
from .service import BackendService, ConflictError, content_hash


class NoArguments(FrozenModel):
    pass


class QuoteArguments(FrozenModel):
    quote_id: str = Field(min_length=1, max_length=64)


class EvidenceArguments(QuoteArguments):
    focus: Literal["COST", "DELIVERY", "TERMS", "ALL"] = "ALL"


DECISION_TOOLS = {
    "read_decision_overview": (NoArguments, "读取当前推荐、排序依据及全部供应商的关键结果"),
    "compare_alternatives": (NoArguments, "计算所有方案的成本、交期与阻碍差异，不改动原报价"),
    "inspect_quote_evidence": (EvidenceArguments, "选择成本、交期或条款主题，核对该供应商报价原文"),
    "inspect_supplier_history": (QuoteArguments, "核查供应商历史表现及数据可用性"),
    "inspect_policy_evidence": (NoArguments, "核查本次冻结的制度检索和合规待核验项"),
    "compile_decision_brief": (NoArguments, "汇总已核查事实、局限及下一步；不构成审批"),
}


class DecisionInvestigationTools:
    def __init__(self, service: BackendService, *, task_id: str, context: dict[str, Any]):
        self.service = service
        self.task_id = task_id
        self.task_revision = context["task_revision"]
        self.graph_run_id = context["graph_run_id"]
        self.result_id = context["result_id"]
        self.result = service.get_result(task_id, self.result_id)
        if not self.result["is_current"]:
            raise ConflictError("investigation_result_stale", "The decision result is no longer current.")
        self.rows = {row["quote_id"]: row for row in self.result["result"]["supplier_results"]}
        self.input_sha256 = content_hash([self.task_revision, self.graph_run_id, self.result_id])
        self.schemas = {
            name: {"description": description, "arguments": model.model_json_schema()}
            for name, (model, description) in DECISION_TOOLS.items()
        }

    def requested_case(self, *, request_id: str, question: str | None = None) -> InvestigationCase:
        task = self.service.get_task(self.task_id)
        identity = content_hash([self.graph_run_id, self.result_id, self.task_revision, request_id])
        return InvestigationCase(
            case_id="decision_" + identity[:32], task_id=self.task_id,
            task_revision=self.task_revision, graph_run_id=self.graph_run_id,
            kind="DECISION", quote_id=None, quote_version=None,
            impact_input_sha256=self.input_sha256,
            policy_binding=task.get("policy_binding") or {},
            goal=("针对用户的问题核查本次冻结决策：" + question.strip()[:400]
                  if question and question.strip() else
                  "核查当前供应商推荐与有竞争力的备选，针对发现的差异选择报价证据、历史表现或制度依据，形成可追溯的决策说明。"),
            known_facts={"requested_investigation": True, "result_id": self.result_id,
                         "quote_ids": list(self.rows)},
            unknown_fields=(), impact_status="REQUIRES_INVESTIGATION",
        )

    def current(self) -> bool:
        task = self.service.get_task(self.task_id)
        return (task["task_revision"] == self.task_revision
                and task["current_graph_run_id"] == self.graph_run_id
                and task["current_result_id"] == self.result_id)

    def save(self, case: InvestigationCase) -> None:
        self.service.append_artifact(
            task_id=self.task_id, task_revision=self.task_revision,
            artifact_type="INVESTIGATION_CASE", schema_version=case.schema_version,
            payload=case.model_dump(mode="json"), quote_id=None,
            graph_run_id=self.graph_run_id,
        )

    def prepare_case(self, case: InvestigationCase) -> InvestigationCase:
        """Collect mandatory baseline facts before the model chooses follow-up checks."""
        for name in ("read_decision_overview", "compare_alternatives"):
            result = self.execute(case, name, {})
            if result.status != "OK":
                raise ConflictError("investigation_baseline_unavailable", "Decision baseline is unavailable.")
            case = case.model_copy(update={"observations": case.observations + (
                ToolObservation(sequence=len(case.observations) + 1,
                                reason="系统基线核查", arguments={}, result=result, latency_ms=0),
            )})
            self.save(case)
        return case

    @staticmethod
    def clarification_cards(_case: InvestigationCase) -> tuple[dict[str, Any], ...]:
        return ()

    @staticmethod
    def resolution(case: InvestigationCase) -> str | None:
        return "REQUEST_COMPLETED" if any(
            observation.result.tool_name == "compile_decision_brief" and observation.result.status == "OK"
            for observation in case.observations
        ) else None

    def finalize_on_stop(self, case: InvestigationCase) -> ToolResult | None:
        if "compile_decision_brief" not in self.available_schemas(case):
            return None
        return self.execute(case, "compile_decision_brief", {})

    def available_schemas(self, case: InvestigationCase) -> dict[str, dict]:
        observed = {o.result.tool_name for o in case.observations if o.result.status == "OK"}
        if "read_decision_overview" not in observed:
            return {"read_decision_overview": self.schemas["read_decision_overview"]}
        if "compare_alternatives" not in observed:
            return {"compare_alternatives": self.schemas["compare_alternatives"]}
        used = {o.result.tool_name for o in case.observations if o.result.status in {"OK", "NOT_FOUND"}}
        result = {name: schema for name, schema in self.schemas.items()
                  if name in {"inspect_quote_evidence", "inspect_supplier_history"}
                  or (name == "inspect_policy_evidence" and name not in used
                      and bool(self.result["input_snapshot"] and self.result["input_snapshot"].get("policy_set_version")))}
        if any(o.result.tool_name in {"inspect_quote_evidence", "inspect_supplier_history", "inspect_policy_evidence"}
               and o.result.status == "OK" for o in case.observations):
            result["compile_decision_brief"] = self.schemas["compile_decision_brief"]
        return result

    def call_signature(self, _case: InvestigationCase, name: str, arguments: dict) -> str:
        try:
            parsed = DECISION_TOOLS[name][0].model_validate(arguments)
            normalized = parsed.model_dump(mode="json")
        except (KeyError, ValidationError):
            normalized = arguments
        return content_hash([name, normalized])

    def execute(self, case: InvestigationCase, name: str, arguments: dict) -> ToolResult:
        common = dict(tool_name=name, task_id=self.task_id, task_revision=self.task_revision,
                      quote_id=None, input_sha256=self.input_sha256)
        if not self.current():
            return ToolResult(**common, status="STALE", error_code="input_changed")
        if (case.kind != "DECISION" or case.task_id != self.task_id or case.task_revision != self.task_revision
                or case.graph_run_id != self.graph_run_id or case.impact_input_sha256 != self.input_sha256):
            return ToolResult(**common, status="DENIED", error_code="tool_scope_denied")
        if name not in self.available_schemas(case):
            return ToolResult(**common, status="DENIED", error_code="tool_not_available")
        try:
            args = DECISION_TOOLS[name][0].model_validate(arguments)
        except ValidationError:
            return ToolResult(**common, status="DENIED", error_code="tool_arguments_invalid")
        quote_id = getattr(args, "quote_id", None)
        if quote_id is not None and quote_id not in self.rows:
            return ToolResult(**common, status="DENIED", error_code="quote_out_of_scope")

        if name == "read_decision_overview":
            payload = self.result["result"]
            data = {
                "recommended_quote_ids": payload["recommended_quote_ids"],
                "final_recommendation_allowed": payload["final_recommendation_allowed"],
                "ranking_preference": (self.result.get("input_snapshot") or {}).get("decision_profile", {}).get("preferences", {}).get("primary_criterion")
                if (self.result.get("input_snapshot") or {}).get("decision_profile") else None,
                "suppliers": [{key: row.get(key) for key in ("quote_id", "supplier_name", "status", "total_cost", "estimated_arrival_date")}
                              for row in self.rows.values()],
                "policy_bound": bool((self.result.get("input_snapshot") or {}).get("policy_set_version")),
            }
            return ToolResult(**common, status="OK", data=data)

        if name == "compare_alternatives":
            gaps = self.service.selection_gaps(self.task_id, expected_task_revision=self.task_revision,
                                                expected_result_id=self.result_id)["gaps"]
            return ToolResult(**common, status="OK", data={"gaps": [
                {key: gap.get(key) for key in ("quote_id", "status", "confirmed_total_cost", "cost_difference_vs_other",
                                                "delivery_days_late", "failed_reasons", "pending_reasons", "assumptions")}
                for gap in gaps
            ]})

        if name == "inspect_quote_evidence":
            fields = self.service.list_quote_fields(self.task_id, quote_id, result_id=self.result_id)["fields"]
            topics = {
                "COST": ("price", "fee", "tax", "shipping", "currency", "discount"),
                "DELIVERY": ("lead", "delivery", "valid", "quantity", "moq"),
                "TERMS": ("payment", "warranty", "condition", "substitut", "package", "revision"),
            }
            selected = [f for f in fields if args.focus == "ALL" or any(
                token in f["field_name"] for token in topics[args.focus]
            )]
            selected = selected[:5]
            return ToolResult(**common, status="OK" if selected else "NOT_FOUND", data={
                "quote_id": quote_id,
                "focus": args.focus,
                "fields": [{
                    "field_name": field.get("field_name"),
                    "normalized_value": field.get("normalized_value"),
                    "validation_status": field.get("validation_status"),
                    "evidence": [{key: (value[:500] if key == "quoted_text" and isinstance(value, str) else value)
                                  for key, value in source.items() if key in {"source_id", "page_number", "row_number", "quoted_text"}}
                                 for source in (field.get("evidence") or [])[:3]],
                } for field in selected],
                "truncated": len(fields) > len(selected),
            })

        if name == "inspect_supplier_history":
            info = self.service.supplier_information(self.task_id, result_id=self.result_id)
            supplier = next((item for item in info["suppliers"] if any(q["quote_id"] == quote_id for q in item["quotes"])), None)
            data = {"quote_id": quote_id, "data_availability": info.get("data_availability"),
                    "history_binding": info.get("history_binding"),
                    "supplier": {key: supplier.get(key) for key in ("display_name", "identity_match_status",
                                                                    "history_availability_status", "history_snapshot")}
                    if supplier else None}
            return ToolResult(**common, status="OK" if supplier and supplier.get("history_snapshot") else "NOT_FOUND", data=data)

        if name == "inspect_policy_evidence":
            compliance = self.result["policy_compliance"]
            return ToolResult(**common, status="OK", data={
                "retrievals": [{
                    "status": retrieval.get("status"),
                    "covered_control_codes": retrieval.get("covered_control_codes"),
                    "missing_control_codes": retrieval.get("missing_control_codes"),
                    "citations": [{key: citation.get(key) for key in ("citation_id", "control_code", "title", "section", "text")}
                                  for citation in retrieval.get("citations", [])[:3]],
                } for retrieval in self.result["policy_retrievals"][:10]],
                "compliance": {"recommendation_scope": compliance.get("recommendation_scope"),
                               "requires_human_review": compliance.get("requires_human_review"),
                               "counts": compliance.get("counts"),
                               "assessments": [{"quote_id": item.get("quote_id"), "status": item.get("status"),
                                                "checks": [{key: check.get(key) for key in ("control_code", "status", "reason_code", "citation_ids")}
                                                           for check in item.get("checks", [])]}
                                               for item in compliance.get("assessments", [])]},
            })

        observations = [o for o in case.observations if o.result.status in {"OK", "NOT_FOUND"}]
        if not any(o.result.tool_name == "compare_alternatives" for o in observations) or not any(
            o.result.tool_name in {"inspect_quote_evidence", "inspect_supplier_history", "inspect_policy_evidence"}
            and o.result.status == "OK"
            for o in observations
        ):
            return ToolResult(**common, status="DENIED", error_code="investigation_incomplete")
        return ToolResult(**common, status="OK", data={
            "recommended_quote_ids": self.result["result"]["recommended_quote_ids"],
            "checked_tools": [o.result.tool_name for o in observations],
            "checked_quote_ids": sorted({o.arguments.get("quote_id") for o in observations if o.arguments.get("quote_id")}),
            "facts": [{"tool": o.result.tool_name, "data": o.result.data} for o in observations
                      if o.result.tool_name != "read_decision_overview"],
            "boundary": "仅解释已冻结的采购比较；不修改需求、报价、制度或审批状态。",
        })
