"""Schema and citation validation for model output.

Rules
-----
* The response must match the expected schema (Pydantic validation).
* Every cited evidence ID must be an alias present in the bundle; unknown IDs are
  removed and counted as fabricated.
* Hypotheses about services that are not candidates in the bundle are removed.
* If more than half of all citations are fabricated the whole response is rejected.
* Suggested checks that would change a system are labelled as needing human approval:
  TraceMind only ever *suggests*; it never executes anything.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, ValidationError

from app.llm.evidence import EvidenceBundle

MUTATING = re.compile(r"\b(restart|roll ?back|revert|delete|drop|kill|scale|redeploy|deploy|truncate|purge|failover|rm\s|shutdown)\b", re.I)
APPROVAL_PREFIX = "[Requires human approval] "


class CitationError(ValueError):
    pass


class LLMHypothesis(BaseModel):
    service: str = Field(max_length=160)
    cause_type: str = Field(max_length=64)
    explanation: str = Field(max_length=2000)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class LLMSummary(BaseModel):
    summary: str = Field(max_length=4000)
    hypotheses: list[LLMHypothesis] = Field(max_length=8)
    suggested_checks: list[str] = Field(default_factory=list, max_length=12)
    uncertainties: list[str] = Field(default_factory=list, max_length=12)


class LLMAnswer(BaseModel):
    answer: str = Field(max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    uncertainties: list[str] = Field(default_factory=list, max_length=10)
    suggested_checks: list[str] = Field(default_factory=list, max_length=10)
    insufficient_evidence: bool = False


def _strict_schema(model: type[BaseModel]) -> dict:
    """JSON schema with additionalProperties=false and every property required, which is
    what constrained decoding expects."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def tighten(node: dict) -> dict:
        if "$ref" in node:  # inline references so the schema is self-contained
            node = dict(defs[node["$ref"].rsplit("/", 1)[-1]])
        node = {k: v for k, v in node.items() if k not in ("maxLength", "maxItems", "default", "title")}
        if node.get("type") == "object" and "properties" in node:
            node["properties"] = {name: tighten(prop) for name, prop in node["properties"].items()}
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        if isinstance(node.get("items"), dict):
            node["items"] = tighten(node["items"])
        return node

    return tighten(schema)


SUMMARY_SCHEMA = _strict_schema(LLMSummary)
ANSWER_SCHEMA = _strict_schema(LLMAnswer)


def _check(text: str) -> str:
    text = text.strip()
    return APPROVAL_PREFIX + text if MUTATING.search(text) and not text.startswith(APPROVAL_PREFIX) else text


def _map_ids(ids: list[str], bundle: EvidenceBundle, rejected: list[str]) -> list[str]:
    mapped: list[str] = []
    for raw in ids:
        alias = str(raw).strip().upper()
        if alias in bundle.alias_to_event:
            event_id = bundle.alias_to_event[alias]
            if event_id not in mapped:
                mapped.append(event_id)
        else:
            rejected.append(str(raw)[:40])
    return mapped


def validate_summary(raw: dict, bundle: EvidenceBundle) -> tuple[dict, dict]:
    """Return (clean summary with real event IDs, validation report)."""
    try:
        parsed = LLMSummary.model_validate(raw)
    except ValidationError as exc:
        raise CitationError(f"schema validation failed: {exc.error_count()} errors") from exc
    rejected: list[str] = []
    dropped_services: list[str] = []
    total = 0
    hypotheses = []
    for h in parsed.hypotheses:
        total += len(h.supporting_evidence_ids) + len(h.contradicting_evidence_ids)
        if h.service not in bundle.candidate_services:
            dropped_services.append(h.service[:60])
            rejected.extend(h.supporting_evidence_ids + h.contradicting_evidence_ids)
            continue
        hypotheses.append({
            "service": h.service,
            "cause_type": h.cause_type,
            "explanation": h.explanation.strip(),
            "supporting_evidence_ids": _map_ids(h.supporting_evidence_ids, bundle, rejected),
            "contradicting_evidence_ids": _map_ids(h.contradicting_evidence_ids, bundle, rejected),
        })
    if total and len(rejected) / total > 0.5:
        raise CitationError(f"{len(rejected)} of {total} citations reference unknown evidence")
    uncertainties = [u.strip() for u in parsed.uncertainties if u.strip()]
    if rejected:
        uncertainties.append(f"{len(rejected)} citation(s) produced by the model did not match supplied evidence and were removed.")
    if dropped_services:
        uncertainties.append("Hypotheses naming services outside the analysed incident were removed.")
    report = {"citations_total": total, "citations_rejected": len(rejected), "rejected_ids": rejected[:20],
              "dropped_services": dropped_services}
    return {
        "summary": parsed.summary.strip(),
        "hypotheses": hypotheses,
        "suggested_checks": [_check(c) for c in parsed.suggested_checks if c.strip()],
        "uncertainties": uncertainties,
    }, report


def validate_answer(raw: dict, bundle: EvidenceBundle) -> tuple[dict, dict]:
    try:
        parsed = LLMAnswer.model_validate(raw)
    except ValidationError as exc:
        raise CitationError(f"schema validation failed: {exc.error_count()} errors") from exc
    rejected: list[str] = []
    evidence = _map_ids(parsed.evidence_ids, bundle, rejected)
    total = len(parsed.evidence_ids)
    if total and len(rejected) / total > 0.5:
        raise CitationError(f"{len(rejected)} of {total} citations reference unknown evidence")
    uncertainties = [u.strip() for u in parsed.uncertainties if u.strip()]
    if rejected:
        uncertainties.append(f"{len(rejected)} citation(s) did not match supplied evidence and were removed.")
    # An answer whose every citation was fabricated has, in effect, no evidence behind it.
    insufficient = parsed.insufficient_evidence or (total > 0 and not evidence)
    return {
        "answer": parsed.answer.strip(),
        "evidence_ids": evidence,
        "uncertainties": uncertainties,
        "suggested_checks": [_check(c) for c in parsed.suggested_checks if c.strip()],
        "insufficient_evidence": insufficient,
    }, {"citations_total": total, "citations_rejected": len(rejected), "rejected_ids": rejected[:20]}
