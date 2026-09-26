"""Read-only, result-bound tools for one-click decision investigation."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import Field, ValidationError

from supplier_comparison.rules.contracts import FrozenModel

from .investigation import InvestigationCase, ToolObservation, ToolResult
from .response_language import response_language
from .service import BackendService, ConflictError, content_hash


class NoArguments(FrozenModel):
    pass


class QuoteArguments(FrozenModel):
    quote_id: str = Field(min_length=1, max_length=64)


class EvidenceArguments(QuoteArguments):
    focus: Literal["COST", "DELIVERY", "TERMS", "ALL"] = "ALL"


DECISION_TOOLS = {
    "read_decision_overview": (NoArguments, "Read the current recommendation, ranking basis, and key results for every supplier"),
    "compare_alternatives": (NoArguments, "Calculate cost, delivery, and blocking differences across all options without changing source quotations"),
    "inspect_quote_evidence": (EvidenceArguments, "Choose a cost, delivery, or terms focus and review the supplier's source quotation"),
    "inspect_supplier_history": (QuoteArguments, "Review supplier historical performance and data availability"),
    "inspect_policy_evidence": (NoArguments, "Review frozen policy retrieval and compliance items requiring verification"),
    "compile_decision_brief": (NoArguments, "Summarise verified facts, limitations, and next steps; this does not constitute approval"),
}


def _supplier_name_mentioned(name: str, text: str) -> bool:
    """Match a supplier's full name or a distinctive leading name token."""

    normalized_name = name.casefold().strip()
    normalized_text = text.casefold()
    if normalized_name and normalized_name in normalized_text:
        return True
    generic = {"components", "circuits", "company", "limited", "ltd", "supplier"}
    distinctive = [
        token for token in re.findall(r"[a-z0-9]+", normalized_name)
        if len(token) >= 4 and token not in generic
    ]
    return bool(distinctive and distinctive[0] in normalized_text)


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

    def _ranking_preference(self) -> str:
        snapshot = self.result.get("input_snapshot") or {}
        profile = snapshot.get("decision_profile") or {}
        preferences = profile.get("preferences") or {}
        return str(
            preferences.get("primary_criterion")
            or (snapshot.get("requirement") or {}).get("ranking_preference")
            or ""
        )

    def _investigation_plan(self, question: str | None = None) -> dict[str, Any]:
        language = response_language(question)
        criterion = self._ranking_preference()
        if criterion == "FASTEST_CONFIRMED_DELIVERY":
            plan = {
                "criterion": criterion,
                "quote_focus": "DELIVERY",
                "requires_quote_evidence": True,
                "requires_supplier_history": True,
                "evidence_topics": ["source delivery terms", "historical on-time rate"],
                "reason": "Fastest delivery requires both the current delivery commitment and historical fulfilment reliability to be verified.",
            }
        elif criterion == "LOWEST_CONFIRMED_TOTAL_COST":
            plan = {
                "criterion": criterion,
                "quote_focus": "COST",
                "requires_quote_evidence": True,
                "requires_supplier_history": False,
                "evidence_topics": ["price", "shipping fee", "tax"],
                "reason": "Lowest cost requires source evidence for every component of total cost.",
            }
        elif criterion in {
            "HIGHEST_SUPPLIER_PERFORMANCE",
            "HIGHEST_HISTORICAL_ON_TIME_RATE",
            "LOWEST_HISTORICAL_REJECTED_LINE_RATE",
        }:
            plan = {
                "criterion": criterion,
                "quote_focus": None,
                "requires_quote_evidence": False,
                "requires_supplier_history": True,
                "evidence_topics": ["supplier grade", "historical on-time rate", "rejection rate"],
                "reason": "Supplier-performance ranking requires historical performance records and their availability to be verified.",
            }
        elif criterion == "LONGEST_CONFIRMED_PAYMENT_TERM":
            plan = {
                "criterion": criterion,
                "quote_focus": "TERMS",
                "requires_quote_evidence": True,
                "requires_supplier_history": False,
                "evidence_topics": ["source payment terms"],
                "reason": "Payment-term ranking requires the payment clause in the quotation to be verified.",
            }
        else:
            plan = {
                "criterion": criterion or "UNSPECIFIED",
                "quote_focus": "ALL",
                "requires_quote_evidence": True,
                "requires_supplier_history": True,
                "evidence_topics": ["key quotation fields", "supplier historical performance"],
                "reason": "The ranking basis is unclear; review key evidence for the recommendation and alternatives.",
            }

        lowered = (question or "").casefold()
        asks_cost = any(token in lowered for token in ("成本", "价格", "便宜", "总金额", "运费", "税费", "cost", "price", "freight", "tax"))
        asks_delivery = any(token in lowered for token in ("交期", "到货", "交付", "delivery", "arrival", "lead time"))
        asks_terms = any(token in lowered for token in ("付款", "账期", "质保", "条款", "payment", "warranty", "terms"))
        asks_quote_source = any(token in lowered for token in (
            "核对报价", "报价原文", "报价内容", "原始文件", "原文件", "source quotation",
            "source quote", "original quotation", "original quote", "source document",
            "quotation content", "quote content", "original file",
        ))
        asks_history = any(token in lowered for token in (
            "历史", "准时率", "拒收", "供应商表现", "供应商记录", "履约风险", "评级",
            "history", "historical", "on-time", "rejection", "performance", "rating", "supplier record", "fulfilment risk",
        ))
        asks_policy = any(token in lowered for token in (
            "制度", "合规", "审批", "证明", "材料", "规则", "准入", "过期", "不匹配", "补传", "人工确认",
            "policy", "compliance", "approval", "rohs", "eligibility", "expired", "mismatch",
            "manual confirmation", "human confirmation",
        )) or any(token in lowered for token in (
            "未被推荐", "没被推荐", "没有被推荐", "不能推荐", "not recommended", "not selected", "cannot be recommended",
        ))
        asks_conflict = any(token in lowered for token in (
            "冲突", "矛盾", "不一致", "conflict", "contradiction", "inconsisten",
        ))
        asks_gaps = any(token in lowered for token in (
            "缺失", "过期", "不匹配", "无法确认", "补传", "人工确认",
            "missing", "expired", "mismatch", "cannot confirm", "manual confirmation", "human confirmation",
        ))
        asks_delta = any(token in lowered for token in (
            "贵多少", "便宜多少", "早多少", "晚多少", "差多少", "溢价",
            "how much more", "how much less", "premium", "difference",
        ))
        asks_ranking = any(token in lowered for token in (
            "哪家", "哪些", "哪两", "最好", "最快", "最低", "最值得比较",
            "which", "best", "fastest", "earliest", "lowest", "compare most",
        ))
        asks_recommendation = any(token in lowered for token in (
            "推荐", "未选", "没选", "recommend", "not selected",
        ))
        asks_validation = any(token in lowered for token in (
            "核对", "查证", "可靠吗", "是否", "真的", "检查",
            "verify", "reliable", "is the", "check", "validate",
        ))
        requested_focuses = {
            focus for asked, focus in (
                (asks_cost, "COST"), (asks_delivery, "DELIVERY"), (asks_terms, "TERMS")
            ) if asked
        }
        if requested_focuses or asks_quote_source:
            plan["requires_quote_evidence"] = True
            plan["quote_focus"] = (
                next(iter(requested_focuses)) if len(requested_focuses) == 1 and not asks_quote_source
                else "ALL"
            )
        if asks_history:
            plan["requires_supplier_history"] = True
        # The user's explicit scope takes precedence over the current ranking
        # criterion.  A history-only question should not reread quotation
        # fields, and a policy-only question should not perform unrelated
        # quotation/history calls.
        if asks_history and not requested_focuses and not asks_quote_source:
            plan["requires_quote_evidence"] = False
            plan["quote_focus"] = None
        if asks_policy and not requested_focuses and not asks_quote_source and not asks_history:
            plan["requires_quote_evidence"] = False
            plan["requires_supplier_history"] = False
            plan["quote_focus"] = None
        plan["requires_policy_evidence"] = asks_policy
        if asks_gaps:
            analysis_goal = "GAP_REVIEW"
        elif asks_conflict:
            analysis_goal = "CONFLICT_REVIEW"
        elif asks_delta:
            analysis_goal = "DELTA_COMPARISON"
        elif asks_policy and not (asks_cost or asks_delivery or asks_history):
            analysis_goal = "COMPLIANCE_REVIEW"
        elif asks_ranking and not asks_recommendation:
            analysis_goal = "RANKING_COMPARISON"
        elif asks_recommendation:
            analysis_goal = "RECOMMENDATION_EXPLANATION"
        elif asks_validation:
            analysis_goal = "EVIDENCE_VALIDATION"
        else:
            analysis_goal = "EVIDENCE_REVIEW"
        plan["analysis_goal"] = analysis_goal
        plan["dimensions"] = [
            dimension for enabled, dimension in (
                (asks_cost, "COST"),
                (asks_delivery, "DELIVERY"),
                (asks_history, "HISTORY"),
                (asks_policy, "COMPLIANCE"),
            ) if enabled
        ]
        plan["requests_two_candidates"] = bool(
            any(token in lowered for token in (
                "哪两家", "哪两份", "两家供应商", "两份报价",
                "which two", "two suppliers", "two quotes", "two quotations",
            ))
        )
        plan["response_language"] = language
        if language == "zh":
            topic_labels = {
                "source delivery terms": "报价原文中的交付条款",
                "historical on-time rate": "历史准时率",
                "price": "价格",
                "shipping fee": "运费",
                "tax": "税费",
                "supplier grade": "供应商评级",
                "rejection rate": "拒收率",
                "source payment terms": "报价原文中的付款条款",
                "key quotation fields": "报价关键字段",
                "supplier historical performance": "供应商历史表现",
            }
            plan["evidence_topics"] = [
                topic_labels.get(str(topic), str(topic))
                for topic in plan.get("evidence_topics", [])
            ]
            reason_labels = {
                "FASTEST_CONFIRMED_DELIVERY": "最快到货需要同时核对当前交付承诺和历史履约可靠性。",
                "LOWEST_CONFIRMED_TOTAL_COST": "最低成本需要核对总成本各组成项的原始依据。",
                "HIGHEST_SUPPLIER_PERFORMANCE": "供应商表现排序需要核对历史表现记录及其数据可用性。",
                "HIGHEST_HISTORICAL_ON_TIME_RATE": "历史准时率排序需要核对供应商历史履约记录。",
                "LOWEST_HISTORICAL_REJECTED_LINE_RATE": "历史拒收率排序需要核对供应商历史质量记录。",
                "LONGEST_CONFIRMED_PAYMENT_TERM": "付款账期排序需要核对报价原文中的付款条款。",
            }
            plan["reason"] = reason_labels.get(
                criterion,
                "当前排序依据不够明确，需要核对推荐项与备选项的关键证据。",
            )
        return plan

    def requested_case(self, *, request_id: str, question: str | None = None) -> InvestigationCase:
        task = self.service.get_task(self.task_id)
        identity = content_hash([self.graph_run_id, self.result_id, self.task_revision, request_id])
        normalized_question = question.strip()[:400] if question and question.strip() else None
        plan = self._investigation_plan(normalized_question)
        language = response_language(normalized_question)
        return InvestigationCase(
            case_id="decision_" + identity[:32], task_id=self.task_id,
            task_revision=self.task_revision, graph_run_id=self.graph_run_id,
            kind="DECISION", quote_id=None, quote_version=None,
            impact_input_sha256=self.input_sha256,
            policy_binding=task.get("policy_binding") or {},
            goal=(("围绕用户问题核查当前冻结的决策结果：" if language == "zh" else
                   "Investigate this frozen decision in response to the user's question: ") + normalized_question
                  if normalized_question else
                  "Review the current supplier recommendation and competitive alternatives, selecting quotation evidence, historical performance, or policy basis for identified differences to produce a traceable decision explanation."),
            known_facts={"requested_investigation": True, "result_id": self.result_id,
                         "quote_ids": list(self.rows), "question": normalized_question,
                         "response_language": language,
                         "ranking_investigation_plan": plan},
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
        language = str(case.known_facts.get("response_language") or "en")
        for name in ("read_decision_overview", "compare_alternatives"):
            result = self.execute(case, name, {})
            if result.status != "OK":
                raise ConflictError("investigation_baseline_unavailable", "Decision baseline is unavailable.")
            case = case.model_copy(update={"observations": case.observations + (
                ToolObservation(sequence=len(case.observations) + 1,
                                reason=("系统基线核查" if language == "zh" else "System baseline review"),
                                arguments={}, result=result, latency_ms=0),
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

    def next_required_check(self, case: InvestigationCase) -> tuple[str, dict[str, Any], str] | None:
        """Return one server-planned check when the model becomes unavailable.

        The scope was already derived from the user's general intent before any
        model call. This method only completes that declared scope; it does not
        invent a new investigation plan.
        """

        available = self.available_schemas(case)
        language = str(case.known_facts.get("response_language") or "en")
        if "inspect_quote_evidence" in available:
            schema = available["inspect_quote_evidence"]
            quote_ids = schema.get("available_quote_ids") or []
            if quote_ids:
                return (
                    "inspect_quote_evidence",
                    {
                        "quote_id": str(quote_ids[0]),
                        "focus": str(schema.get("preferred_focus") or "ALL"),
                    },
                    "规划模型不可用，按已声明的核查范围继续读取报价证据"
                    if language == "zh" else
                    "The planning model is unavailable; continue reading quotation evidence within the declared scope",
                )
        if "inspect_supplier_history" in available:
            quote_ids = available["inspect_supplier_history"].get("available_quote_ids") or []
            if quote_ids:
                return (
                    "inspect_supplier_history",
                    {"quote_id": str(quote_ids[0])},
                    "规划模型不可用，按已声明的核查范围继续读取供应商历史"
                    if language == "zh" else
                    "The planning model is unavailable; continue reading supplier history within the declared scope",
                )
        if "inspect_policy_evidence" in available:
            return (
                "inspect_policy_evidence",
                {},
                "规划模型不可用，按已声明的核查范围继续读取制度与证明材料"
                if language == "zh" else
                "The planning model is unavailable; continue reading policy and evidence within the declared scope",
            )
        return None

    def _candidate_quote_ids(self, case: InvestigationCase) -> list[str]:
        """Keep a decision check focused on the recommendation and one useful challenger."""
        question = str(case.known_facts.get("question") or "").casefold()
        mentioned = [
            quote_id for quote_id, row in self.rows.items()
            if _supplier_name_mentioned(str(row.get("supplier_name") or ""), question)
        ]
        asks_lowest = any(token in question for token in (
            "最低", "最便宜", "最低报价", "lowest", "cheapest", "lowest quote",
        ))
        if mentioned and asks_lowest:
            lowest = min(
                (
                    row for row in self.rows.values()
                    if str(row.get("status") or "").upper() == "FEASIBLE"
                    and row.get("total_cost") is not None
                ),
                key=lambda row: (Decimal(str(row["total_cost"])), str(row.get("supplier_name") or "")),
                default=None,
            )
            selected = mentioned + ([str(lowest["quote_id"])] if lowest else [])
            return list(dict.fromkeys(selected))
        if mentioned and not any(token in question for token in (
            "推荐", "未选", "没选", "比较", "相比", "为什么",
            "recommend", "not selected", "compare", "versus", "why",
        )):
            return mentioned

        candidates: list[str] = []
        for quote_id in self.result["result"].get("recommended_quote_ids", []):
            if quote_id in self.rows and quote_id not in candidates:
                candidates.append(quote_id)

        requests_two_delivery_candidates = (
            bool(any(token in question for token in (
                "哪两家", "哪两份", "两家供应商", "两份报价",
                "which two quotes", "which two quotations", "which two suppliers", "two quotes", "two quotations", "two suppliers",
            )))
            and any(token in question for token in (
                "到货", "交期", "交付", "最快", "最早", "delivery", "arrival", "fastest", "earliest",
            ))
        )
        if requests_two_delivery_candidates:
            delivery_ranked = sorted(
                (
                    row for row in self.rows.values()
                    if str(row.get("status") or "").upper() == "FEASIBLE"
                    and row.get("estimated_arrival_date")
                ),
                key=lambda row: (
                    str(row["estimated_arrival_date"]),
                    Decimal(str(row.get("total_cost") or "Infinity")),
                    str(row.get("supplier_name") or row.get("quote_id")),
                ),
            )
            selected = [str(row["quote_id"]) for row in delivery_ranked[:2]]
            if len(selected) == 2:
                return selected
        requests_all_suppliers = (
            any(token in question for token in (
                "哪家供应商", "哪些供应商", "所有供应商", "各家供应商", "四家供应商",
                "which supplier", "which suppliers", "all suppliers", "each supplier", "among the suppliers",
            ))
            and not any(token in question for token in ("哪两家", "两家供应商", "which two", "two suppliers"))
        )
        if requests_all_suppliers:
            ranked = [
                str(quote_id)
                for group in self.result["result"].get("ranked_quote_ids", [])
                for quote_id in (group if isinstance(group, (list, tuple)) else [group])
            ]
            return list(dict.fromkeys(
                ranked + [str(row["quote_id"]) for row in self.rows.values()]
            ))
        plan = case.known_facts.get("ranking_investigation_plan") or {}
        if plan.get("analysis_goal") == "GAP_REVIEW" and not mentioned:
            return [str(row["quote_id"]) for row in self.rows.values()]
        if candidates and question and any(
            token in question for token in (
                "当前推荐", "推荐报价", "推荐供应商",
                "current recommendation", "recommended quote", "recommended supplier",
            )
        ) and not any(str(row.get("supplier_name") or "").casefold() in question for row in self.rows.values()):
            return candidates[:1]

        goal = case.goal.casefold()
        mentioned = [
            quote_id for quote_id, row in self.rows.items()
            if _supplier_name_mentioned(str(row.get("supplier_name") or ""), goal)
        ]
        ranked = [
            str(quote_id)
            for group in self.result["result"].get("ranked_quote_ids", [])
            for quote_id in (group if isinstance(group, (list, tuple)) else [group])
        ]
        feasible = [str(row["quote_id"]) for row in self.rows.values() if row.get("status") == "FEASIBLE"]
        for quote_id in mentioned + ranked + feasible:
            if quote_id not in candidates:
                candidates.append(quote_id)
            if len(candidates) >= 2:
                break
        return candidates or list(self.rows)[:2]

    @staticmethod
    def _checked_quote_ids(case: InvestigationCase, tool_name: str) -> set[str]:
        return {
            str(observation.arguments["quote_id"])
            for observation in case.observations
            if observation.result.tool_name == tool_name
            and observation.result.status in {"OK", "NOT_FOUND"}
            and observation.arguments.get("quote_id")
        }

    @staticmethod
    def _percentage(value: Any) -> str | None:
        try:
            return f"{Decimal(str(value)) * Decimal('100'):.2f}%"
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _verified_findings(
        self, observations: list[ToolObservation], plan: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Separate confirmed advantages from confirmed adverse facts.

        A completed investigation can still contain known risks. These findings are
        deliberately independent from unresolved evidence gaps.
        """
        advantages: list[dict[str, Any]] = []
        risks: list[dict[str, Any]] = []
        recommended = set(self.result["result"].get("recommended_quote_ids", []))
        criterion = str(plan.get("criterion") or "")
        language = str(plan.get("response_language") or "en")

        for quote_id in recommended:
            row = self.rows.get(str(quote_id)) or {}
            supplier = row.get("supplier_name") or quote_id
            if criterion == "FASTEST_CONFIRMED_DELIVERY" and row.get("estimated_arrival_date"):
                advantages.append({
                    "category": "DELIVERY",
                    "quote_id": quote_id,
                    "supplier_name": supplier,
                    "summary": (
                        f"{supplier} 是当前推荐项，确认交期最快，预计到货日期为 {row['estimated_arrival_date']}。"
                        if language == "zh" else
                        f"{supplier} is the recommended option with the fastest confirmed delivery; estimated arrival is {row['estimated_arrival_date']}."
                    ),
                })
            elif criterion == "LOWEST_CONFIRMED_TOTAL_COST" and row.get("total_cost") is not None:
                advantages.append({
                    "category": "COST",
                    "quote_id": quote_id,
                    "supplier_name": supplier,
                    "summary": (
                        f"{supplier} 是当前推荐项，确认总成本最低，为 {row['total_cost']}。"
                        if language == "zh" else
                        f"{supplier} is the recommended option with the lowest confirmed total cost of {row['total_cost']}."
                    ),
                })

        for observation in observations:
            if observation.result.tool_name != "inspect_supplier_history" or observation.result.status != "OK":
                continue
            data = observation.result.data
            quote_id = str(data.get("quote_id") or "")
            supplier_name = str(data.get("supplier_name") or quote_id or "Relevant supplier")
            supplier = data.get("supplier") or {}
            history = supplier.get("history_snapshot") or {}
            if not isinstance(history, dict):
                continue
            grade = history.get("overall_grade")
            availability = str(history.get("history_availability_status") or "")
            on_time = history.get("on_time") or {}
            rejected = history.get("rejected_lines") or {}
            on_time_rate = self._percentage(on_time.get("rate")) if isinstance(on_time, dict) else None
            rejected_rate = self._percentage(rejected.get("rate")) if isinstance(rejected, dict) else None

            favorable: list[str] = []
            adverse: list[str] = []
            if on_time_rate == "100.00%":
                favorable.append("历史准时率为 100.00%" if language == "zh" else "historical on-time rate 100.00%")
            elif on_time_rate:
                adverse.append(f"历史准时率为 {on_time_rate}" if language == "zh" else f"historical on-time rate {on_time_rate}")
            if grade in {"A", "B"}:
                favorable.append(f"综合评级为 {grade}" if language == "zh" else f"overall grade {grade}")
            elif grade in {"C", "D"}:
                adverse.append(f"综合评级为 {grade}" if language == "zh" else f"overall grade {grade}")
            elif grade == "N" or availability in {"INSUFFICIENT_SAMPLE", "NO_DATA", "OUT_OF_SCOPE"}:
                adverse.append(
                    "历史样本不足或不在适用范围内"
                    if language == "zh" else
                    "historical sample is insufficient or outside the applicable scope"
                )
            if rejected_rate and rejected_rate != "0.00%":
                adverse.append(f"历史拒收率为 {rejected_rate}" if language == "zh" else f"historical rejection rate {rejected_rate}")
            elif rejected_rate == "0.00%":
                favorable.append("历史拒收率为 0.00%" if language == "zh" else "historical rejection rate 0.00%")

            if favorable:
                advantages.append({
                    "category": "SUPPLIER_HISTORY",
                    "quote_id": quote_id,
                    "supplier_name": supplier_name,
                    "summary": f"{supplier_name}：{'、'.join(favorable)}。" if language == "zh" else f"{supplier_name}: {', '.join(favorable)}.",
                })
            if adverse:
                risks.append({
                    "category": "SUPPLIER_HISTORY",
                    "quote_id": quote_id,
                    "supplier_name": supplier_name,
                    "summary": f"{supplier_name}：{'、'.join(adverse)}。" if language == "zh" else f"{supplier_name}: {', '.join(adverse)}.",
                })

        for observation in observations:
            if observation.result.tool_name != "compare_alternatives" or observation.result.status != "OK":
                continue
            for gap in observation.result.data.get("gaps") or []:
                if not isinstance(gap, dict):
                    continue
                quote_id = str(gap.get("quote_id") or "")
                supplier_name = str(self.rows.get(quote_id, {}).get("supplier_name") or quote_id or "Relevant supplier")
                failed = gap.get("failed_reasons") or []
                if failed:
                    risks.append({
                        "category": "QUOTE_FEASIBILITY",
                        "quote_id": quote_id,
                        "supplier_name": supplier_name,
                        "summary": (
                            f"{supplier_name} 存在已确认的不合规项。"
                            if language == "zh" else
                            f"{supplier_name} has a confirmed non-compliant item."
                        ),
                    })

        unique_advantages = list({item["summary"]: item for item in advantages}.values())
        unique_risks = list({item["summary"]: item for item in risks}.values())
        return unique_advantages, unique_risks

    def should_finalize(self, case: InvestigationCase) -> bool:
        """Stop chasing unchanged facts once the requested risk has been bounded."""
        follow_ups = [
            observation for observation in case.observations
            if observation.result.tool_name.startswith("inspect_")
            and observation.result.status in {"OK", "NOT_FOUND"}
        ]
        if not follow_ups:
            return False
        if any(observation.result.status == "DENIED" for observation in case.observations):
            return True
        candidates = set(self._candidate_quote_ids(case))
        evidence_checked = self._checked_quote_ids(case, "inspect_quote_evidence")
        history_checked = self._checked_quote_ids(case, "inspect_supplier_history")
        plan = case.known_facts.get("ranking_investigation_plan") or self._investigation_plan()
        quote_complete = not plan.get("requires_quote_evidence") or candidates <= evidence_checked
        history_complete = not plan.get("requires_supplier_history") or candidates <= history_checked
        policy_required = bool(plan.get("requires_policy_evidence"))
        policy_checked = any(
            observation.result.tool_name == "inspect_policy_evidence"
            and observation.result.status in {"OK", "NOT_FOUND"}
            for observation in case.observations
        )
        return (quote_complete and history_complete and (not policy_required or policy_checked)) or len(follow_ups) >= 5

    def fallback_on_premature_stop(self, case: InvestigationCase) -> tuple[str, dict[str, Any], str] | None:
        """Choose one safe evidence read when a model stops before observing any evidence."""
        available = self.available_schemas(case)
        question = str(case.known_facts.get("question") or "").casefold()
        language = str(case.known_facts.get("response_language") or "en")
        plan = case.known_facts.get("ranking_investigation_plan") or self._investigation_plan(question)
        if "inspect_policy_evidence" in available and any(
            token in question for token in ("制度", "合规", "审批", "证明", "policy", "compliance", "approval", "evidence", "rohs")
        ):
            reason = ("模型过早停止；继续核对与问题相关的冻结制度及合规证据" if language == "zh" else
                      "The model stopped too early; review frozen policy and compliance evidence relevant to the question")
            return "inspect_policy_evidence", {}, reason
        if "inspect_supplier_history" in available and any(
            token in question for token in ("历史", "准时率", "拒收", "供应商表现", "history", "on-time", "rejection", "performance")
        ):
            quote_id = available["inspect_supplier_history"]["available_quote_ids"][0]
            reason = ("模型过早停止；继续核对与问题相关的供应商历史表现" if language == "zh" else
                      "The model stopped too early; review supplier historical performance relevant to the question")
            return "inspect_supplier_history", {"quote_id": quote_id}, reason
        if "inspect_quote_evidence" in available:
            if any(token in question for token in ("交期", "到货", "交付", "delivery", "arrival", "lead time")):
                focus = "DELIVERY"
            elif any(token in question for token in ("付款", "质保", "条款", "payment", "warranty", "terms")):
                focus = "TERMS"
            elif any(token in question for token in ("成本", "价格", "便宜", "金额", "cost", "price")):
                focus = "COST"
            else:
                focus = str(plan.get("quote_focus") or "ALL")
            quote_id = available["inspect_quote_evidence"]["available_quote_ids"][0]
            reason = ("模型过早停止；继续核对最相关的报价原文证据" if language == "zh" else
                      "The model stopped too early; add one review of the most relevant quotation evidence")
            return "inspect_quote_evidence", {"quote_id": quote_id, "focus": focus}, reason
        if "inspect_supplier_history" in available and plan.get("requires_supplier_history"):
            quote_id = available["inspect_supplier_history"]["available_quote_ids"][0]
            reason = ("模型过早停止；继续核对当前排序依据要求的供应商历史表现" if language == "zh" else
                      "The model stopped too early; review supplier historical performance required by the current ranking basis")
            return "inspect_supplier_history", {"quote_id": quote_id}, reason
        return None

    def available_schemas(self, case: InvestigationCase) -> dict[str, dict]:
        observed = {o.result.tool_name for o in case.observations if o.result.status == "OK"}
        if "read_decision_overview" not in observed:
            return {"read_decision_overview": self.schemas["read_decision_overview"]}
        if "compare_alternatives" not in observed:
            return {"compare_alternatives": self.schemas["compare_alternatives"]}
        used = {o.result.tool_name for o in case.observations if o.result.status in {"OK", "NOT_FOUND"}}
        candidates = self._candidate_quote_ids(case)
        evidence_remaining = [
            quote_id for quote_id in candidates
            if quote_id not in self._checked_quote_ids(case, "inspect_quote_evidence")
        ]
        history_remaining = [
            quote_id for quote_id in candidates
            if quote_id not in self._checked_quote_ids(case, "inspect_supplier_history")
        ]
        result: dict[str, dict] = {}
        plan = case.known_facts.get("ranking_investigation_plan") or self._investigation_plan()
        if evidence_remaining and plan.get("requires_quote_evidence"):
            focus = str(plan.get("quote_focus") or "ALL")
            result["inspect_quote_evidence"] = self.schemas["inspect_quote_evidence"] | {
                "available_quote_ids": evidence_remaining,
                "preferred_focus": focus,
                "guidance": f"The current primary criterion requires {focus} review of: {', '.join(plan.get('evidence_topics') or [])}. Review each quotation only once.",
            }
        if history_remaining and plan.get("requires_supplier_history"):
            result["inspect_supplier_history"] = self.schemas["inspect_supplier_history"] | {
                "available_quote_ids": history_remaining,
                "guidance": f"The current primary criterion requires review of: {', '.join(plan.get('evidence_topics') or [])}.",
            }
        policy_bound = bool(
            self.result["input_snapshot"]
            and self.result["input_snapshot"].get("policy_set_version")
        )
        policy_requested = bool(plan.get("requires_policy_evidence"))
        if "inspect_policy_evidence" not in used and (policy_requested or policy_bound):
            result["inspect_policy_evidence"] = self.schemas["inspect_policy_evidence"]
        candidates_set = set(candidates)
        quote_complete = not plan.get("requires_quote_evidence") or candidates_set <= self._checked_quote_ids(case, "inspect_quote_evidence")
        history_complete = not plan.get("requires_supplier_history") or candidates_set <= self._checked_quote_ids(case, "inspect_supplier_history")
        policy_complete = not plan.get("requires_policy_evidence") or "inspect_policy_evidence" in used
        if quote_complete and history_complete and policy_complete:
            result["compile_decision_brief"] = self.schemas["compile_decision_brief"]
        return result

    def call_signature(self, _case: InvestigationCase, name: str, arguments: dict) -> str:
        try:
            parsed = DECISION_TOOLS[name][0].model_validate(arguments)
            normalized = parsed.model_dump(mode="json")
        except (KeyError, ValidationError):
            normalized = arguments
        # One evidence read returns a bounded, reusable snapshot for that quote. Treat a
        # later focus change as the same call instead of rereading the same document.
        if name == "inspect_quote_evidence" and isinstance(normalized, dict):
            normalized = {"quote_id": normalized.get("quote_id")}
        return content_hash([name, normalized])

    def accepts_call(self, case: InvestigationCase, name: str, arguments: dict) -> bool:
        """Validate a model-selected call against the server's current scope.

        The model may only choose among the schemas and quote IDs offered for
        this exact observation state. Invalid choices are planning errors, not
        tool executions; the runner can safely replace them with the next
        server-planned check without recording a misleading denied tool call.
        """

        available = self.available_schemas(case)
        schema = available.get(name)
        if schema is None:
            return False
        try:
            parsed = DECISION_TOOLS[name][0].model_validate(arguments)
        except (KeyError, ValidationError):
            return False
        quote_id = getattr(parsed, "quote_id", None)
        available_quote_ids = schema.get("available_quote_ids") or []
        if available_quote_ids and quote_id not in available_quote_ids:
            return False
        preferred_focus = schema.get("preferred_focus")
        selected_focus = getattr(parsed, "focus", None)
        if preferred_focus and selected_focus not in {preferred_focus, "ALL"}:
            return False
        return True

    def execute(self, case: InvestigationCase, name: str, arguments: dict) -> ToolResult:
        common = dict(tool_name=name, task_id=self.task_id, task_revision=self.task_revision,
                      quote_id=None, input_sha256=self.input_sha256)
        language = str(case.known_facts.get("response_language") or "en")
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
                "ranking_preference": self._ranking_preference() or None,
                "investigation_plan": case.known_facts.get("ranking_investigation_plan") or self._investigation_plan(),
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
            by_name = {str(field.get("field_name")): field for field in fields}
            essential_by_focus = {
                "ALL": (
                    "supplier_name", "manufacturer_part_number", "currency", "unit_price",
                    "price_basis_quantity", "shipping_fee_amount", "other_fees_amount", "tax_mode",
                    "lead_time_days", "delivery_semantics", "payment_terms", "valid_until",
                ),
                "COST": (
                    "currency", "unit_price", "price_basis_quantity", "price_basis_unit",
                    "shipping_fee_status", "shipping_fee_amount", "other_fees_status",
                    "other_fees_amount", "tax_mode", "tax_rate", "discount_amount",
                ),
                "DELIVERY": (
                    "lead_time_days", "lead_time_unit", "delivery_semantics",
                    "estimated_arrival_date", "valid_until", "moq_quantity",
                ),
                "TERMS": (
                    "payment_terms", "warranty_terms", "commercial_conditions",
                    "substitution_terms", "package_terms", "quote_revision",
                ),
            }
            essential = essential_by_focus.get(args.focus, ())
            if essential:
                selected = [by_name[name] for name in essential if name in by_name]
            else:
                selected = [f for f in fields if any(
                    token in f["field_name"] for token in topics[args.focus]
                )]
            matching_count = len(selected)
            selected = selected[:12]
            return ToolResult(**common, status="OK" if selected else "NOT_FOUND", data={
                "quote_id": quote_id,
                "supplier_name": self.rows[quote_id].get("supplier_name"),
                "focus": args.focus,
                "fields": [{
                    "field_name": field.get("field_name"),
                    "normalized_value": field.get("normalized_value"),
                    "validation_status": field.get("validation_status"),
                    "evidence": [{key: (value[:500] if key == "quoted_text" and isinstance(value, str) else value)
                                  for key, value in source.items() if key in {"source_id", "page_number", "row_number", "quoted_text"}}
                                 for source in (field.get("evidence") or [])[:3]],
                } for field in selected],
                "truncated": matching_count > len(selected),
            })

        if name == "inspect_supplier_history":
            info = self.service.supplier_information(self.task_id, result_id=self.result_id)
            supplier = next((item for item in info["suppliers"] if any(q["quote_id"] == quote_id for q in item["quotes"])), None)
            data = {"quote_id": quote_id, "supplier_name": self.rows[quote_id].get("supplier_name"),
                    "data_availability": info.get("data_availability"),
                    "history_binding": info.get("history_binding"),
                    "supplier": {key: supplier.get(key) for key in ("display_name", "identity_match_status",
                                                                    "history_availability_status", "history_snapshot")}
                    if supplier else None}
            return ToolResult(**common, status="OK" if supplier and supplier.get("history_snapshot") else "NOT_FOUND", data=data)

        if name == "inspect_policy_evidence":
            policy_snapshot = self.result.get("input_snapshot") or {}
            if not policy_snapshot.get("policy_set_version"):
                return ToolResult(**common, status="NOT_FOUND", data={
                    "reason_code": "POLICY_NOT_BOUND",
                    "retrievals": [],
                    "compliance": {
                        "recommendation_scope": None,
                        "requires_human_review": True,
                        "counts": {},
                        "assessments": [],
                    },
                })
            compliance = self.result["policy_compliance"]
            candidate_quote_ids = set(self._candidate_quote_ids(case))
            assessments = [
                item for item in compliance.get("assessments", [])
                if str(item.get("quote_id") or "") in candidate_quote_ids
            ]
            scoped_counts = {
                status: sum(1 for item in assessments if item.get("status") == status)
                for status in ("COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED", "NOT_EVALUATED")
            }
            return ToolResult(**common, status="OK", data={
                "retrievals": [{
                    "status": retrieval.get("status"),
                    "covered_control_codes": retrieval.get("covered_control_codes"),
                    "missing_control_codes": retrieval.get("missing_control_codes"),
                    "citations": [{key: citation.get(key) for key in ("citation_id", "control_code", "title", "section", "text")}
                                  for citation in retrieval.get("citations", [])[:3]],
                } for retrieval in self.result["policy_retrievals"][:10]],
                "compliance": {"recommendation_scope": compliance.get("recommendation_scope"),
                               "requires_human_review": any(
                                   check.get("status") == "REVIEW_REQUIRED"
                                   for item in assessments for check in item.get("checks", [])
                               ),
                               "counts": scoped_counts,
                               "assessments": [{"quote_id": item.get("quote_id"), "status": item.get("status"),
                                                "checks": [{key: check.get(key) for key in (
                                                    "control_code", "status", "reason_code", "reason_codes",
                                                    "citation_ids", "evidence_ids", "source_refs", "execution_stage",
                                                    "triggered", "approval_confirmed",
                                                )}
                                                           for check in item.get("checks", [])]}
                                               for item in assessments]},
            })

        observations = [o for o in case.observations if o.result.status in {"OK", "NOT_FOUND"}]
        if not any(o.result.tool_name == "compare_alternatives" for o in observations) or not any(
            o.result.tool_name in {"inspect_quote_evidence", "inspect_supplier_history", "inspect_policy_evidence"}
            and o.result.status in {"OK", "NOT_FOUND"}
            for o in observations
        ):
            return ToolResult(**common, status="DENIED", error_code="investigation_incomplete")
        unresolved: list[dict[str, Any]] = []
        seen_unresolved: set[tuple[str, str, str]] = set()
        for observation in observations:
            if (
                observation.result.tool_name == "inspect_policy_evidence"
                and observation.result.status == "NOT_FOUND"
            ):
                key = ("", "POLICY", str(observation.result.data.get("reason_code") or "NOT_FOUND"))
                if key not in seen_unresolved:
                    seen_unresolved.add(key)
                    unresolved.append({
                        "type": "POLICY_EVIDENCE_NOT_FOUND",
                        "reason_code": observation.result.data.get("reason_code"),
                        "action": (
                            "先为当前任务绑定并发布适用制度，再重新分析"
                            if language == "zh" else
                            "Bind and publish the applicable policy for this task, then analyse again"
                        ),
                    })
            elif observation.result.tool_name == "inspect_policy_evidence":
                for assessment in (observation.result.data.get("compliance") or {}).get("assessments", []):
                    for check in assessment.get("checks", []):
                        if check.get("status") not in {"REVIEW_REQUIRED", "MISSING", "CONFLICT"}:
                            continue
                        key = (str(assessment.get("quote_id") or ""), str(check.get("control_code") or ""),
                               str(check.get("reason_code") or ""))
                        if key in seen_unresolved:
                            continue
                        seen_unresolved.add(key)
                        unresolved.append({
                            "type": "POLICY_EVIDENCE",
                            "quote_id": assessment.get("quote_id"),
                            "supplier_name": self.rows.get(str(assessment.get("quote_id")), {}).get("supplier_name"),
                            "control_code": check.get("control_code"),
                            "reason_code": check.get("reason_code"),
                            "action": ("补充对应的证明或审批记录后重新分析" if language == "zh" else
                                       "Add the corresponding evidence or approval record, then analyse again"),
                        })
            elif observation.result.status == "NOT_FOUND":
                quote = str(observation.arguments.get("quote_id") or "")
                key = (quote, observation.result.tool_name, "NOT_FOUND")
                if key not in seen_unresolved:
                    seen_unresolved.add(key)
                    unresolved.append({
                        "type": "EVIDENCE_NOT_FOUND",
                        "quote_id": quote or None,
                        "supplier_name": self.rows.get(quote, {}).get("supplier_name"),
                        "source": observation.result.tool_name,
                        "action": ("补充缺失数据后重新分析" if language == "zh" else
                                   "Add missing data, then analyse again"),
                    })

        facts: list[dict[str, Any]] = []
        seen_facts: set[str] = set()
        for observation in observations:
            if observation.result.tool_name == "read_decision_overview":
                continue
            signature = self.call_signature(case, observation.result.tool_name, observation.arguments)
            if signature in seen_facts:
                continue
            seen_facts.add(signature)
            facts.append({"tool": observation.result.tool_name, "data": observation.result.data})

        plan = case.known_facts.get("ranking_investigation_plan") or self._investigation_plan()
        verified_advantages, verified_risks = self._verified_findings(observations, plan)
        if unresolved:
            stop_reason = (
                "所需核查已经完成；剩余事项需要外部证据或人工记录，重复调用现有工具无法解决。"
                if language == "zh" else
                "Required checks are complete. Remaining items need external evidence or manual records and cannot be resolved by repeatedly calling the available tools."
            )
        else:
            stop_reason = (
                "当前排序依据要求的核查已经完成；未发现证据缺失或冲突，已核实风险仍保留在结论中。"
                if language == "zh" else
                "Checks required by the current ranking basis are complete. No missing or conflicting evidence was found; verified risks remain in the conclusion."
            )

        return ToolResult(**common, status="OK", data={
            "recommended_quote_ids": self.result["result"]["recommended_quote_ids"],
            "checked_tools": [o.result.tool_name for o in observations],
            "checked_quote_ids": sorted({o.arguments.get("quote_id") for o in observations if o.arguments.get("quote_id")}),
            "facts": facts,
            "verified_advantages": verified_advantages,
            "verified_risks": verified_risks,
            "requires_follow_up": bool(unresolved),
            "unresolved_items": unresolved,
            "stop_reason": stop_reason,
            "boundary": (
                "仅解释冻结的采购比较结果，不修改需求、报价、制度或审批状态。"
                if language == "zh" else
                "Explain only the frozen procurement comparison; do not change requirements, quotations, policy, or approval status."
            ),
        })
