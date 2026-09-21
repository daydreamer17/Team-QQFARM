"""Server-scoped wrappers: no SQL/path/identity arguments are exposed to the model."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, ValidationError
from sqlalchemy import select

from supplier_comparison.rag.contracts import RetrievalRequest, RetrievalStatus
from supplier_comparison.rules.contracts import FrozenModel

from .investigation import InvestigationCase, ToolResult
from .models import WorkflowArtifact
from .service import BackendService, ConflictError, content_hash


class NoArguments(FrozenModel):
    pass


class FieldArguments(FrozenModel):
    field_names: tuple[str, ...] = Field(default=(), max_length=10)


class PolicyArguments(FrozenModel):
    query: str = Field(min_length=1, max_length=512)
    control_code: str = Field(min_length=1, max_length=128)


TOOLS = {
    "get_task_context": (NoArguments, "读取当前需求、版本和本轮允许的工具"),
    "analyze_decision_impact": (NoArguments, "读取程序刚生成的影响证明，不自行重算"),
    "get_comparison_result": (NoArguments, "读取本轮程序比较结果，可能尚不可发布"),
    "get_cost_breakdown": (NoArguments, "读取程序成本明细与已知小计，不猜测未知费用"),
    "locate_quote_source": (FieldArguments, "核对当前报价字段原文；缺失时提供整份文件的有限文本预览"),
    "get_confirmed_quote_records": (FieldArguments, "只找本任务当前报价文件仍适用的人工确认记录"),
    "request_clarification": (NoArguments, "生成本轮需核对字段卡片，由用户批量纠正；不写回事实"),
    "retrieve_policy": (PolicyArguments, "检索任务冻结制度；不能代替必查制度门禁"),
    "analyze_selection_gap": (NoArguments, "程序计算本报价的全部阻塞项、成本与到货差距以及有明确假设的条件试算"),
    "draft_clarification": (NoArguments, "生成未发送的供应商沟通草稿；不能修改需求、报价或承诺入选"),
    "simulate_requirement_change": (NoArguments, "只按服务器记录的用户授权参数进行假设试算，不能自行改变正式需求"),
}


class ScopedInvestigationTools:
    def __init__(self, service: BackendService, *, task_id: str, graph_run_id: str,
                 task_revision: int, impact_artifact_id: str | None, evaluated_at: datetime,
                 policy_retriever=None, authorized_requirement_changes=None):
        self.service = service
        self.task = service.get_task(task_id)  # Authorization before any internal artifact read.
        self.task_id, self.graph_run_id, self.task_revision = task_id, graph_run_id, task_revision
        self.review = service.list_review_problems(task_id)
        self.quotes = {q["quote_id"]: q for q in self.review["quotes"]}
        self.policy_binding = self.task["policy_binding"] or {}
        self.impact = None
        if impact_artifact_id:
            with service.session_factory() as session:
                artifact = session.get(WorkflowArtifact, impact_artifact_id)
                if (artifact is None or artifact.task_id != task_id or artifact.graph_run_id != graph_run_id
                        or artifact.task_revision != task_revision or artifact.artifact_type != "DECISION_IMPACT_RESULT"):
                    raise ConflictError("investigation_impact_scope_invalid", "Impact report does not belong to these inputs.")
                self.impact = dict(artifact.payload)
        self.evaluated_at = evaluated_at
        self.policy_retriever = policy_retriever
        from supplier_comparison.rules import RequirementChanges
        self.authorized_requirement_changes = (RequirementChanges.model_validate(authorized_requirement_changes)
                                               if authorized_requirement_changes is not None else None)
        self.fingerprint = self._fingerprint(self.task, self.review)
        self.input_sha256 = self.impact["input_sha256"] if self.impact else self.fingerprint
        self.schemas = {name: {"description": description, "arguments": model.model_json_schema()}
                        for name, (model, description) in TOOLS.items()
                        if name != 'simulate_requirement_change' or self.authorized_requirement_changes is not None
                        if name != "retrieve_policy" or (policy_retriever and all(self.policy_binding.values()) and len(self.policy_binding) == 4)}
        if self.authorized_requirement_changes is not None:
            self.schemas['simulate_requirement_change']['authorization'] = {
                'changes': self.authorized_requirement_changes.model_dump(
                    mode='json', exclude_none=True, exclude_defaults=True
                ),
                'arguments_are_server_injected': True, 'formal_requirement_unchanged': True,
            }

    @staticmethod
    def _fingerprint(task: dict, review: dict) -> str:
        return content_hash({
            "revision": task["task_revision"], "graph": task["current_graph_run_id"],
            "binding": task["policy_binding"], "requirement": task["requirement"],
            # Content identity survives re-execution that creates equivalent
            # artifact IDs; changed facts or evidence still invalidate the case.
            "quotes": [{k: q[k] for k in ("quote_id", "quote_version", "document_id", "fields", "evidence_sources", "review_status")}
                       for q in review["quotes"]],
        })

    def current(self) -> bool:
        task = self.service.get_task(self.task_id)
        return (task["task_revision"] == self.task_revision
                and task["current_graph_run_id"] == self.graph_run_id
                and self._fingerprint(task, self.service.list_review_problems(self.task_id)) == self.fingerprint)

    def available_schemas(self, case: InvestigationCase) -> dict[str, dict]:
        """Do not keep offering completed immutable, argument-free reads."""
        completed = {o.result.tool_name for o in case.observations
                     if o.result.status in {'OK', 'NOT_FOUND'}
                     and o.result.tool_name in TOOLS and TOOLS[o.result.tool_name][0] is NoArguments}
        return {name: schema for name, schema in self.schemas.items() if name not in completed}

    def call_signature(self, case: InvestigationCase, name: str, arguments: dict) -> str:
        try:
            args = TOOLS[name][0].model_validate(arguments)
            normalized = args.model_dump(mode="json")
            if isinstance(args, FieldArguments):
                normalized["field_names"] = sorted(set(args.field_names or case.unknown_fields))
            if isinstance(args, PolicyArguments):
                normalized["query"] = " ".join(args.query.split())
                normalized["control_code"] = args.control_code.strip().upper()
        except (KeyError, ValidationError):
            normalized = arguments
        return content_hash([name, normalized])

    def cases(self) -> tuple[InvestigationCase, ...]:
        impacts = {i["quote_id"]: i for i in (self.impact or {}).get("quote_impacts", [])}
        rows = {r["quote_id"]: r for r in (self.impact or {}).get("comparison", {}).get("supplier_results", [])}
        cases = []
        for quote_id, quote in sorted(self.quotes.items()):
            if self.impact is not None and quote_id not in rows:
                continue  # Explicitly excluded by the current Decision Profile.
            problems = [p for p in self.review["problems"] if p["quote_id"] == quote_id and p["needs_resolution"]]
            impact = impacts.get(quote_id, {})
            fields = tuple(sorted(set(impact.get("unknown_fields", [])) | {p["field_name"] for p in problems}))
            if not fields and not problems:
                continue
            identity = content_hash([self.graph_run_id, quote_id, self.task_revision, self.input_sha256])
            case = InvestigationCase(
                case_id="case_" + identity[:32], task_id=self.task_id, task_revision=self.task_revision,
                graph_run_id=self.graph_run_id, quote_id=quote_id, quote_version=quote["quote_version"],
                impact_input_sha256=self.input_sha256, policy_binding=self.policy_binding,
                goal="查明该报价的待确认信息是否影响本次推荐，并给出可核对的下一步。",
                known_facts={"comparison": rows.get(quote_id), "impact": impact or None,
                             "problems": problems, "original_filename": quote["original_filename"],
                             "impact_proof_available": self.impact is not None},
                unknown_fields=fields, impact_status=impact.get("status", "UNDETERMINED"),
            )
            # Resume a completed or partially recorded case of these exact inputs.
            with self.service.session_factory() as session:
                history = session.scalars(select(WorkflowArtifact).where(
                    WorkflowArtifact.task_id == self.task_id,
                    WorkflowArtifact.graph_run_id == self.graph_run_id,
                    WorkflowArtifact.task_revision == self.task_revision,
                    WorkflowArtifact.quote_id == quote_id,
                    WorkflowArtifact.artifact_type == "INVESTIGATION_CASE",
                ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc())).all()
                existing = next((a for a in history if a.payload.get("case_id") == case.case_id), None)
                if existing:
                    case = InvestigationCase.model_validate(existing.payload)
            cases.append(case)
        return tuple(cases)

    def save(self, case: InvestigationCase) -> None:
        self.service.append_artifact(
            task_id=self.task_id, task_revision=self.task_revision, artifact_type="INVESTIGATION_CASE",
            schema_version=case.schema_version, payload=case.model_dump(mode="json"),
            quote_id=case.quote_id, graph_run_id=self.graph_run_id,
        )

    def clarification_cards(self, case: InvestigationCase) -> tuple[dict[str, Any], ...]:
        quote = self.quotes[case.quote_id]
        candidates = {f["field_name"]: f for f in quote["fields"]}
        names = set(case.unknown_fields)
        # Entering a missing fee amount needs both status and amount, not a second question.
        for prefix in ("shipping_fee", "other_fees"):
            if names.intersection({prefix + "_status", prefix + "_amount"}):
                names.update({prefix + "_status", prefix + "_amount"})

        def resolution(name: str) -> str:
            matching = [
                problem
                for problem in self.review["problems"]
                if problem["quote_id"] == case.quote_id
                and problem["field_name"] == name
            ]
            if any(
                problem["resolution"] == "ADDITIONAL_INFORMATION_REQUIRED"
                for problem in matching
            ):
                return "ADDITIONAL_INFORMATION_REQUIRED"
            if name in candidates and not any(
                problem["resolution"] == "REEXTRACT_OR_SYSTEM_REPAIR"
                for problem in matching
            ):
                return "FIELD_CORRECTION"
            return "REEXTRACT_OR_SYSTEM_REPAIR"

        return tuple({
            "quote_id": case.quote_id, "field_name": name,
            "expected_field_version": candidates.get(name, {}).get("field_version"),
            "document_id": quote["document_id"], "original_filename": quote["original_filename"],
            "source_refs": candidates.get(name, {}).get("source_refs", []),
            "current_value": candidates.get(name, {}).get("normalized_value"),
            "question": "请核对指定原文件或供应商确认信息，再提交真实字段值；未知金额不能填零。",
            "resolution": resolution(name),
        } for name in sorted(names))

    def execute(self, case: InvestigationCase, name: str, arguments: dict) -> ToolResult:
        common = dict(tool_name=name, task_id=self.task_id, task_revision=self.task_revision,
                      quote_id=case.quote_id, input_sha256=self.input_sha256)
        if not self.current():
            return ToolResult(**common, status="STALE", error_code="input_changed")
        if (case.task_id != self.task_id or case.graph_run_id != self.graph_run_id
                or case.task_revision != self.task_revision or case.impact_input_sha256 != self.input_sha256
                or case.quote_id not in self.quotes or case.quote_version != self.quotes[case.quote_id]["quote_version"]):
            return ToolResult(**common, status="DENIED", error_code="tool_scope_denied")
        if name not in self.schemas:
            return ToolResult(**common, status="DENIED", error_code="tool_not_allowed")
        try:
            args = TOOLS[name][0].model_validate(arguments)
        except ValidationError:
            return ToolResult(**common, status="DENIED", error_code="tool_arguments_invalid")
        quote = self.quotes[case.quote_id]
        fields = {f["field_name"]: f for f in quote["fields"]}
        names = getattr(args, "field_names", ()) or case.unknown_fields
        if any(n not in fields for n in names) and isinstance(args, FieldArguments):
            return ToolResult(**common, status="DENIED", error_code="tool_field_not_allowed")
        comparison = (self.impact or {}).get("comparison")
        if name == "get_task_context":
            return ToolResult(**common, status="OK", data={"requirement": self.task["requirement"],
                              "policy_binding": self.policy_binding, "allowed_tools": list(self.schemas),
                              'authorized_requirement_changes': (self.authorized_requirement_changes.model_dump(
                                  mode='json', exclude_none=True, exclude_defaults=True
                              )
                                                                 if self.authorized_requirement_changes is not None else None)})
        if name == "analyze_decision_impact":
            return ToolResult(**common, status="OK" if self.impact else "NOT_FOUND", data={"report": self.impact})
        if name == "get_comparison_result":
            return ToolResult(**common, status="OK" if comparison else "NOT_FOUND", data={"comparison": comparison})
        if name == "get_cost_breakdown":
            row = next((r for r in (comparison or {}).get("supplier_results", []) if r["quote_id"] == case.quote_id), None)
            return ToolResult(**common, status="OK" if row else "NOT_FOUND", data={"evaluation": row})
        if name == "locate_quote_source":
            ids = {ref["source_id"] for n in names for ref in fields[n].get("source_refs", [])}
            pool = quote["evidence_sources"]
            selected = [s for s in pool if s["source_id"] in ids] if ids else pool
            sources = []
            used = 0
            for source in selected:
                text = source.get("raw_text") or ""
                if used >= 12000 or len(sources) >= 40:
                    break
                snippet = text[:min(2000, 12000 - used)]
                sources.append(dict(source) | {"raw_text": snippet, "truncated": len(snippet) < len(text),
                                              "document_id": quote["document_id"], "original_filename": quote["original_filename"]})
                used += len(snippet)
            return ToolResult(**common, status="OK" if sources else "NOT_FOUND", sources=tuple(sources),
                              data={"scope": "FIELD_REFERENCES" if ids else "DOCUMENT_PREVIEW",
                                    "preview_complete": len(sources) == len(selected) and not any(s["truncated"] for s in sources),
                                    "absence_not_confirmed": True})
        if name == "get_confirmed_quote_records":
            records = [fields[n] for n in names if fields[n].get("validation_status") == "VERIFIED"
                       and fields[n].get("origin") in {"USER_INPUT", "USER_CORRECTION"}]
            events = self.service.correction_event_payloads_for_batch(quote["batch_artifact_id"]) if records else []
            matched = [event for event in events if any(event["after"]["field_id"] == r["field_id"] for r in records)]
            return ToolResult(**common, status="OK" if matched else "NOT_FOUND", data={"records": matched,
                              "scope": "CURRENT_TASK_QUOTE_DOCUMENT_AND_REQUIREMENT_ONLY"})
        if name == "request_clarification":
            return ToolResult(**common, status="NEEDS_INPUT", data={"cards": self.clarification_cards(case),
                              "submit_to": f"/api/v1/tasks/{self.task_id}/fields/corrections"})
        if name in {'analyze_selection_gap', 'draft_clarification', 'simulate_requirement_change'}:
            from supplier_comparison.rules import analyze_selection_gap, draft_clarification, simulate_requirement_change
            try:
                request = self.service.selection_analysis_input(
                    self.task_id, expected_task_revision=self.task_revision, evaluated_at=self.evaluated_at)
                if name == 'simulate_requirement_change':
                    trial = simulate_requirement_change(request, self.authorized_requirement_changes, user_authorized=True)
                    return ToolResult(**common, status='OK', data=trial.model_dump(mode='json'))
                report = analyze_selection_gap(request)
                gap = next(g for g in report.gaps if g.quote_id == case.quote_id)
                data = gap.model_dump(mode='json') if name == 'analyze_selection_gap' else draft_clarification(gap)
                return ToolResult(**common, status='OK', data=data)
            except ConflictError as exc:
                return ToolResult(**common, status='DENIED', error_code=exc.code)
            except ValueError:
                return ToolResult(**common, status='DENIED', error_code='simulation_change_invalid')
        # Read-only exploratory RAG; cannot populate mandatory policy artifacts.
        try:
            request = RetrievalRequest(
                task_id=self.task_id, task_revision=self.task_revision, snapshot_id=case.case_id,
                policy_set_version=self.policy_binding["policy_set_version"],
                policy_index_version=self.policy_binding["policy_index_version"],
                category=self.policy_binding["category"], region=self.policy_binding["region"],
                evaluated_at=self.evaluated_at, query=args.query, required_control_codes=[args.control_code],
            )
            result = self.policy_retriever.retrieve(request)
            if (result.policy_set_version != request.policy_set_version
                    or result.policy_index_version != request.policy_index_version
                    or (result.status == RetrievalStatus.OK and (
                        args.control_code.upper() not in result.covered_control_codes
                        or not any(c.control_code == args.control_code.upper() for c in result.citations)))):
                return ToolResult(**common, status="ERROR", error_code="policy_retrieval_contract_invalid")
            return ToolResult(**common, status="OK" if result.status == RetrievalStatus.OK else "NEEDS_INPUT",
                              data={"retrieval": result.model_dump(mode="json"), "mandatory_gate_satisfied": False},
                              sources=tuple(c.model_dump(mode="json") for c in result.citations))
        except Exception:
            return ToolResult(**common, status="ERROR", error_code="policy_retrieval_failed")
