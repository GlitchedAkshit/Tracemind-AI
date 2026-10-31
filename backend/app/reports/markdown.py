"""Markdown postmortem/investigation report generation (server-side, frontend-independent)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.analysis.ranking import CAUSE_LABEL
from app.models import AnalysisRun, Anomaly, Hypothesis, HypothesisEvidence, Incident, Project, TelemetryEvent

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|])")


def esc(text: object) -> str:
    """Escape telemetry-derived text so it renders literally (no injected links/HTML/tables)."""
    return _MD_SPECIAL.sub(r"\\\1", str(text if text is not None else "")).replace("\n", " ")


def _ts(value: datetime | str | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def render_report(db: Session, incident: Incident, run: AnalysisRun) -> str:
    project = db.get(Project, incident.project_id)
    hypotheses = list(db.scalars(
        select(Hypothesis).options(selectinload(Hypothesis.evidence).selectinload(HypothesisEvidence.event)
                                   .selectinload(TelemetryEvent.service))
        .where(Hypothesis.analysis_run_id == run.id).order_by(Hypothesis.rank)))
    anomalies = list(db.scalars(select(Anomaly).options(selectinload(Anomaly.service))
                                .where(Anomaly.incident_id == incident.id).order_by(Anomaly.window_start)))
    summary = run.summary or {}
    meta = summary.get("meta", {})
    duration = ((incident.ended_at or incident.started_at) - incident.started_at).total_seconds() / 60

    out: list[str] = []
    w = out.append
    w(f"# Incident report: {esc(incident.title)}")
    w("")
    if project and project.is_demo:
        w("> **Synthetic demo data.** This report was generated from a generated dataset with injected faults.")
        w("")
    w("## 1. Overview")
    w("")
    w("| Field | Value |")
    w("|---|---|")
    w(f"| Project | {esc(project.name if project else '')} |")
    w(f"| Environment | {esc(incident.environment)} |")
    w(f"| Severity | {incident.severity} |")
    w(f"| Status | {incident.status} |")
    w(f"| Started | {_ts(incident.started_at)} |")
    w(f"| Ended | {_ts(incident.ended_at)} |")
    w(f"| Duration | {duration:.0f} min |")
    w(f"| Incident ID | `{incident.id}` |")
    w(f"| Analysis run | `{run.id}` |")
    w("")
    if summary.get("summary"):
        w(f"**Summary.** {esc(summary['summary'])}")
        w("")
    w("## 2. Affected services")
    w("")
    for service in incident.affected_services:
        own = [a for a in anomalies if a.service.name == service]
        kinds = ", ".join(sorted({a.anomaly_type.replace('_', ' ') for a in own})) or "context only"
        w(f"- **{esc(service)}** — {kinds}")
    w("")
    w("## 3. Timeline (observed)")
    w("")
    w("| Time (UTC) | Service | Event |")
    w("|---|---|---|")
    for entry in incident.timeline or []:
        if entry["kind"] == "anomaly_end":
            continue
        ref = f" `{entry['event_id']}`" if entry.get("event_id") else ""
        w(f"| {_ts(entry['at'])[11:19]} | {esc(entry['service'])} | {esc(entry['kind'].replace('_', ' '))}: {esc(entry['title'])}{ref} |")
    w("")
    w("## 4. Ranked hypotheses (inferred)")
    w("")
    w("Ranking scores are relative scores from an interpretable weighted model. They are **not probabilities** "
      "and have not been calibrated. Temporal correlation does not prove causation.")
    w("")
    for h in hypotheses:
        w(f"### {h.rank}. {esc(h.service_name)} — {CAUSE_LABEL.get(h.cause_type, h.cause_type)}")
        w("")
        w(f"Ranking score **{h.ranking_score:.3f}**. {esc(h.summary)}")
        w("")
        comps = ", ".join(f"{k} {v:.2f}" for k, v in h.score_components.items())
        w(f"Score components: {comps}.")
        w("")
        for polarity, label in (("supporting", "Supporting evidence"), ("contradicting", "Contradicting evidence")):
            items = [e for e in h.evidence if e.polarity == polarity]
            w(f"**{label}**")
            w("")
            if not items:
                w("- None found among analysed events.")
            for e in items:
                ev = e.event
                w(f"- `{e.event_id}` {_ts(ev.occurred_at)[11:19]} {esc(ev.service.name)} {ev.event_type}: {esc(e.explanation)}")
            w("")
        w(f"*Uncertainty:* {esc(h.uncertainty)}")
        w("")
    w("## 5. Suggested investigation steps (read-only)")
    w("")
    checks = summary.get("suggested_checks") or (hypotheses[0].suggested_checks if hypotheses else [])
    for c in checks:
        w(f"- [ ] {esc(c)}")
    if not checks:
        w("- No suggestions were produced.")
    w("")
    w("## 6. Uncertainty and limitations")
    w("")
    for u in summary.get("uncertainties", []):
        w(f"- {esc(u)}")
    w("- Findings are limited to the telemetry ingested for this project; unobserved changes (config, infrastructure, "
      "third parties) may not be represented.")
    w("")
    w("## 7. Provenance")
    w("")
    w(f"- Algorithm version: `{run.algorithm_version}`")
    w(f"- Summary writer: `{meta.get('model_version', run.model_version)}` ({meta.get('writer', 'deterministic')})")
    if meta.get("fallback_reason"):
        w(f"- LLM fallback reason: {esc(meta['fallback_reason'])}")
    w(f"- Citations validated: {meta.get('citations_total', 0)} total, {meta.get('citations_rejected', 0)} rejected")
    w(f"- Analysis completed: {_ts(run.completed_at)}")
    w(f"- Strategy: `{run.config.get('strategy', 'hybrid')}`")
    w("")
    return "\n".join(out)


def report_filename(incident: Incident, report_id: uuid.UUID) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", incident.title.lower()).strip("-")[:50]
    return f"tracemind-{slug}-{str(report_id)[:8]}.md"
