"use client";

import { FileDown, Play, ThumbsDown, ThumbsUp } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ServiceChart, type Metric } from "@/components/charts";
import { Empty, ErrorState, KeyValue, Loading, Panel, ScoreBar, SeverityBadge, StatusDot } from "@/components/common";
import { EventRef } from "@/components/event-inspector";
import { ParticleField } from "@/components/particles/ParticleField";
import { DependencyGraph } from "@/components/service-graph";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, ApiError, apiText, downloadText, type AnalysisRun, type Anomaly, type Hypothesis, type Incident,
  type IncidentMetrics, type Report, type ServiceGraph, type StrategyInfo, type TimelineEntry } from "@/lib/api";
import { CAUSE_LABEL, duration, fmtDateTime, fmtTime, humanize } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { Assistant } from "./assistant";

interface HypothesesResponse { analysis_run: AnalysisRun | null; hypotheses: Hypothesis[] }

export function Workspace() {
  const { id } = useParams<{ id: string }>();
  const { can } = useSession();
  const incident = useApi<Incident>(`/incidents/${id}`);
  const hyps = useApi<HypothesesResponse>(`/incidents/${id}/hypotheses`);
  const timeline = useApi<TimelineEntry[]>(`/incidents/${id}/timeline`);
  const anomalies = useApi<Anomaly[]>(`/incidents/${id}/anomalies`);
  const metrics = useApi<IncidentMetrics>(`/incidents/${id}/metrics`);
  const strategies = useApi<StrategyInfo[]>("/evaluation/strategies");
  const graph = useApi<ServiceGraph>(incident.data ? `/projects/${incident.data.project_id}/service-graph?incident_id=${id}` : null);
  const [activeRun, setActiveRun] = useState<AnalysisRun | null>(null);
  const [strategy, setStrategy] = useState("hybrid");
  const [selected, setSelected] = useState(0);
  const [metric, setMetric] = useState<Metric>("error_rate");

  const reloadAfterRun = () => {
    hyps.reload();
    incident.reload();
    graph.reload();
  };

  // stream progress of an in-flight analysis (SSE), falling back to polling
  useEffect(() => {
    if (!activeRun || activeRun.status === "completed" || activeRun.status === "failed") return;
    let closed = false;
    let poll: ReturnType<typeof setInterval> | undefined;
    const finish = (status: string, message?: string | null) => {
      closed = true;
      setActiveRun(null);
      if (status === "completed") {
        toast.success("Analysis completed");
        setSelected(0);
      } else toast.error(`Analysis failed${message ? `: ${message}` : ""}`);
      reloadAfterRun();
    };
    const es = new EventSource(`/api/v1/analysis-runs/${activeRun.id}/events`);
    es.addEventListener("progress", (e) => {
      const p = JSON.parse((e as MessageEvent).data);
      setActiveRun((r) => (r ? { ...r, ...p } : r));
      if (p.status === "completed" || p.status === "failed") {
        es.close();
        finish(p.status, p.error_message);
      }
    });
    es.onerror = () => {
      es.close();
      if (closed) return;
      poll = setInterval(async () => {
        const r = await api<AnalysisRun>(`/analysis-runs/${activeRun.id}`).catch(() => null);
        if (!r) return;
        setActiveRun(r);
        if (r.status === "completed" || r.status === "failed") {
          clearInterval(poll);
          finish(r.status, r.error_message);
        }
      }, 1000);
    };
    return () => {
      es.close();
      if (poll) clearInterval(poll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRun?.id]);

  // resume progress display if an analysis is already running
  useEffect(() => {
    if (incident.data?.analysis_status === "running" || incident.data?.analysis_status === "queued") {
      api<AnalysisRun[]>(`/incidents/${id}/analysis-runs`).then((runs) => {
        const live = runs.find((r) => r.status === "running" || r.status === "queued");
        if (live) setActiveRun(live);
      }).catch(() => undefined);
    }
  }, [incident.data?.analysis_status, id]);

  if (incident.loading && !incident.data) return <Loading label="loading incident" />;
  if (incident.error) return <ErrorState error={incident.error} retry={incident.reload} />;
  const inc = incident.data!;
  const run = hyps.data?.analysis_run ?? null;
  const hypotheses = hyps.data?.hypotheses ?? [];
  const current = hypotheses[selected];
  const summary = run?.summary;

  const analyze = async () => {
    try {
      const r = await api<AnalysisRun>(`/incidents/${id}/analyze`, { method: "POST", json: { strategy } });
      setActiveRun(r);
    } catch (e) {
      toast.error((e as ApiError).message);
    }
  };
  const exportReport = async () => {
    try {
      const report = await api<Report>(`/incidents/${id}/reports`, { method: "POST" });
      const { text, filename } = await apiText(`/reports/${report.id}/markdown`);
      downloadText(text, filename, "text/markdown");
      toast.success("Report generated and downloaded");
    } catch (e) {
      toast.error((e as ApiError).message);
    }
  };
  const setStatus = async (status: string) => {
    try {
      await api(`/incidents/${id}`, { method: "PATCH", json: { status } });
      incident.reload();
    } catch (e) {
      toast.error((e as ApiError).message);
    }
  };

  const deployMarkers = (metrics.data?.deployments ?? []).map((d) => ({ t: d.occurred_at, label: `deploy ${d.service}` }));

  return (
    <div className="space-y-3">
      {/* ------------------------------------------------------------ header */}
      <div className="tm-panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="tm-label"><Link href="/incidents" className="hover:text-white">incidents</Link> / {inc.id.slice(0, 8)}</p>
            <h1 className="mt-1 font-sans text-[18px] leading-snug text-white">{inc.title}</h1>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-dim">
              <SeverityBadge severity={inc.severity} />
              <StatusDot status={inc.status} />
              <span>{inc.environment}</span>
              <span>started {fmtDateTime(inc.started_at)}</span>
              <span>duration {duration(inc.started_at, inc.ended_at)}</span>
              <span>{inc.anomaly_count} anomalies</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">{inc.affected_services.map((s) => <span key={s} className="tm-chip">{s}</span>)}</div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {can("analyst") && (
              <Select value={inc.status} onValueChange={setStatus}>
                <SelectTrigger className="h-8 w-[130px] text-[11px]" aria-label="Incident status"><SelectValue /></SelectTrigger>
                <SelectContent>{["open", "investigating", "resolved"].map((s) => <SelectItem key={s} value={s} className="text-[11px]">{s}</SelectItem>)}</SelectContent>
              </Select>
            )}
            <Select value={strategy} onValueChange={setStrategy}>
              <SelectTrigger className="h-8 w-[170px] text-[11px]" aria-label="Ranking strategy"><SelectValue /></SelectTrigger>
              <SelectContent>{(strategies.data ?? [{ name: "hybrid" } as StrategyInfo]).map((s) => <SelectItem key={s.name} value={s.name} className="text-[11px]">{humanize(s.name)}</SelectItem>)}</SelectContent>
            </Select>
            <Button size="sm" onClick={analyze} disabled={!can("analyst") || !!activeRun}><Play className="size-3.5" /> {run ? "re-analyse" : "run analysis"}</Button>
            <Button size="sm" variant="outline" onClick={exportReport} disabled={!can("analyst") || !run}><FileDown className="size-3.5" /> export report</Button>
          </div>
        </div>
      </div>

      {activeRun && <AnalysisProgress run={activeRun} />}

      {/* ------------------------------------------------------------ summary + hypotheses */}
      <div className="grid gap-3 xl:grid-cols-12">
        <Panel label="investigation summary · inferred" title={summary?.meta ? `written by ${summary.meta.writer === "llm" ? summary.meta.model_version : "the deterministic writer"}` : "No analysis yet"}
          className="xl:col-span-7">
          {hyps.loading && !hyps.data ? <Loading /> : !run ? (
            <Empty title="This incident has not been analysed">Run an analysis to rank root-cause hypotheses. Analysis is deterministic and works without an LLM.</Empty>
          ) : (
            <div className="space-y-3">
              <p className="tm-prose">{summary?.summary}</p>
              {summary?.meta?.fallback_reason && (
                <p className="border-l-2 border-sev-medium pl-2 text-[11px] text-sev-medium">
                  LLM output was not used ({summary.meta.fallback_reason}); this summary is deterministic.
                </p>
              )}
              {summary?.uncertainties && summary.uncertainties.length > 0 && (
                <div>
                  <p className="tm-label mb-1">uncertainty & limitations</p>
                  <ul className="list-disc space-y-0.5 pl-4 font-sans text-[12px] text-foreground/80">{summary.uncertainties.map((u, i) => <li key={i}>{u}</li>)}</ul>
                </div>
              )}
              {summary?.suspicious_event_ids && summary.suspicious_event_ids.length > 0 && (
                <div className="flex flex-wrap items-center gap-1 text-[11px] text-sev-high">
                  Messages that resemble instructions were treated as data: {summary.suspicious_event_ids.map((e) => <EventRef key={e} id={e} />)}
                </div>
              )}
              <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-hairline pt-2 text-[10px] text-dim">
                <span>algorithm {run.algorithm_version}</span><span>strategy {String(run.config.strategy ?? "hybrid")}</span>
                {summary?.meta?.writer === "llm" && (
                  <span>citations {summary.meta.citations_total ?? 0} checked · {summary.meta.citations_rejected ?? 0} rejected</span>
                )}
                <span>completed {fmtDateTime(run.completed_at)}</span>
              </div>
            </div>
          )}
        </Panel>

        <Panel label="ranked hypotheses" title="Ranking score — relative, not a probability" className="xl:col-span-5" bodyClassName="p-0">
          {!hypotheses.length ? <Empty title="No hypotheses yet" /> : (
            <ol>
              {hypotheses.map((h, i) => (
                <li key={h.id}>
                  <button onClick={() => setSelected(i)} aria-pressed={selected === i}
                    className={cn("w-full border-b border-l-2 border-b-hairline/60 px-3 py-2.5 text-left transition-colors",
                      selected === i ? "border-l-lavender bg-accent/50" : "border-l-transparent hover:bg-white/[0.02]")}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[12px] text-white"><span className="text-dim">#{h.rank}</span> {h.service}</span>
                      <span className="tabular-nums text-[12px] text-lavender">{h.ranking_score.toFixed(3)}</span>
                    </div>
                    <p className="mt-0.5 text-[11px] text-dim">{CAUSE_LABEL[h.cause_type] ?? h.cause_type}</p>
                    <ScoreBar value={Math.max(0, h.ranking_score)} className="mt-1.5" />
                  </button>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>

      {/* ------------------------------------------------------------ selected hypothesis */}
      {current && <HypothesisDetail h={current} runId={run!.id} incidentId={id} llm={summary?.hypotheses?.find((x) => x.service === current.service)} />}

      {/* ------------------------------------------------------------ charts */}
      <Panel label="telemetry · observed" title="Affected services around the incident window"
        actions={
          <div className="flex gap-1" role="group" aria-label="Metric">
            {(["error_rate", "latency_p95_ms"] as Metric[]).map((m) => (
              <button key={m} onClick={() => setMetric(m)} aria-pressed={metric === m}
                className={cn("tm-chip", metric === m ? "border-lavender text-lavender" : "text-dim hover:text-white")}>
                {m === "error_rate" ? "error rate" : "p95 latency"}
              </button>
            ))}
          </div>
        }>
        {metrics.loading && !metrics.data ? <Loading /> : metrics.error ? <ErrorState error={metrics.error} /> :
          metrics.data && Object.keys(metrics.data.services).length ? (
            <ServiceChart series={metrics.data.services} metric={metric} height={260} markers={deployMarkers}
              window={{ start: metrics.data.window.incident_start, end: metrics.data.window.incident_end }} />
          ) : <Empty title="No request telemetry for these services" />}
        <p className="mt-1 text-[10px] text-dim">Shaded: incident window. Dashed orange: deployments.</p>
      </Panel>

      {/* ------------------------------------------------------------ timeline + graph */}
      <div className="grid gap-3 xl:grid-cols-12">
        <Panel label="timeline · observed" title={`${timeline.data?.length ?? 0} entries`} className="xl:col-span-5" bodyClassName="max-h-[520px] overflow-y-auto p-0">
          {timeline.loading && !timeline.data ? <div className="px-3"><Loading /></div> : !timeline.data?.length ? <Empty title="No timeline entries" /> : (
            <ol className="relative">
              {timeline.data.map((t, i) => (
                <li key={i} className="flex gap-3 border-b border-hairline/50 px-3 py-1.5 last:border-0">
                  <span className="w-[54px] shrink-0 pt-0.5 tabular-nums text-[10px] text-dim">{fmtTime(t.at)}</span>
                  <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", {
                    deployment: "bg-sev-high", anomaly_start: "bg-lavender", anomaly_end: "bg-dim", first_error: "bg-sev-critical", error_signature: "bg-sev-medium",
                  }[t.kind] ?? "bg-dim")} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="text-[11px]"><span className="text-lavender">{t.service}</span> <span className="text-dim">{humanize(t.kind)}</span></p>
                    <p className="break-words font-sans text-[12px] text-foreground/85">{t.title}</p>
                  </div>
                  {t.event_id && <EventRef id={t.event_id} className="self-start">evt</EventRef>}
                </li>
              ))}
            </ol>
          )}
        </Panel>
        <Panel label="dependency graph · incident scope" title={graph.data?.leading_service ? `leading: ${graph.data.leading_service}` : "services in window"}
          className="xl:col-span-7" bodyClassName="p-0">
          {graph.loading && !graph.data ? <div className="p-3"><Loading /></div> : graph.error ? <div className="p-3"><ErrorState error={graph.error} /></div> :
            graph.data && <DependencyGraph nodes={graph.data.nodes} edges={graph.data.edges} height={520} />}
        </Panel>
      </div>

      {/* ------------------------------------------------------------ assistant + anomalies */}
      <div className="grid gap-3 xl:grid-cols-12">
        <Assistant incidentId={id} canAsk={can("analyst")} hasAnalysis={!!run} className="xl:col-span-7" />
        <Panel label="anomalies · observed" title={`${anomalies.data?.length ?? 0} detections`} className="xl:col-span-5" bodyClassName="max-h-[560px] overflow-y-auto p-0">
          {!anomalies.data?.length ? <Empty title="No anomalies linked" /> : (
            <ul>
              {anomalies.data.map((a) => (
                <li key={a.id} className="border-b border-hairline/60 px-3 py-2 last:border-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px] text-white">{a.service}</span><SeverityBadge severity={a.severity} />
                  </div>
                  <p className="text-[11px] text-foreground/80">{humanize(a.anomaly_type)} · {a.metric}</p>
                  <p className="text-[10px] text-dim">
                    {fmtTime(a.window_start)}–{fmtTime(a.window_end)} · observed {a.observed.toPrecision(3)} vs baseline {a.baseline.toPrecision(3)} · robust score {a.score.toFixed(1)}
                  </p>
                  {typeof a.details.example === "string" && <p className="mt-0.5 truncate font-sans text-[11px] text-dim">“{a.details.example}”</p>}
                  <div className="mt-1 flex flex-wrap gap-1">{a.supporting_event_ids.slice(0, 4).map((e) => <EventRef key={e} id={e} />)}</div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

function AnalysisProgress({ run }: { run: AnalysisRun }) {
  return (
    <div className="tm-panel relative h-40 overflow-hidden" role="status" aria-live="polite">
      <ParticleField className="absolute inset-0 opacity-80" density={0.5} label="Analysis in progress" />
      <div className="absolute inset-0 bg-gradient-to-r from-black via-black/70 to-transparent" aria-hidden />
      <div className="relative flex h-full max-w-md flex-col justify-center gap-2 p-5">
        <p className="tm-label">analysis in progress · {run.status}</p>
        <p className="text-[14px] text-white">{run.stage}</p>
        <Progress value={run.progress} className="h-1" aria-label="Analysis progress" />
        <p className="text-[10px] text-dim">run {run.id.slice(0, 8)} · {run.algorithm_version}</p>
      </div>
    </div>
  );
}

const COMPONENT_LABEL: Record<string, string> = {
  temporal: "temporal precedence", dependency: "dependency relevance", anomaly: "anomaly strength",
  trace: "trace origin", error: "error evidence", deployment: "deployment proximity", contradiction: "contradiction (penalty)",
};

function HypothesisDetail({ h, runId, incidentId, llm }: {
  h: Hypothesis; runId: string; incidentId: string;
  llm?: { explanation: string; supporting_evidence_ids: string[]; contradicting_evidence_ids: string[] };
}) {
  const { can } = useSession();
  const [sent, setSent] = useState<string | null>(null);
  const supporting = h.evidence.filter((e) => e.polarity === "supporting");
  const contradicting = h.evidence.filter((e) => e.polarity === "contradicting");
  const feedback = async (verdict: string) => {
    try {
      await api(`/incidents/${incidentId}/feedback`, { method: "POST", json: { verdict, hypothesis_id: h.id, analysis_run_id: runId } });
      setSent(verdict);
      toast.success("Feedback recorded");
    } catch (e) {
      toast.error((e as ApiError).message);
    }
  };
  const evidenceList = (items: typeof supporting, tone: "support" | "contra") => (
    <ul className="space-y-1.5">
      {items.map((e) => (
        <li key={e.event_id + e.polarity} className={cn("border-l-2 pl-2", tone === "support" ? "border-ok/60" : "border-sev-critical/60")}>
          <div className="flex flex-wrap items-center gap-2 text-[10px] text-dim">
            <EventRef id={e.event_id}>{fmtTime(e.occurred_at)} · {e.service} · {e.event_type}</EventRef>
            <span>{e.severity}</span>
          </div>
          <p className="mt-0.5 font-sans text-[12px] text-foreground/90">{e.explanation}</p>
          {e.message && <p className="truncate text-[10px] text-dim">“{e.message}”</p>}
        </li>
      ))}
    </ul>
  );
  return (
    <div className="grid gap-3 xl:grid-cols-12">
      <Panel label={`hypothesis #${h.rank} · inferred`} title={`${h.service} — ${CAUSE_LABEL[h.cause_type] ?? h.cause_type}`} className="xl:col-span-4">
        <p className="tm-prose text-[13px]">{llm?.explanation || h.summary}</p>
        <p className="tm-label mb-1.5 mt-4">score components</p>
        <ul className="space-y-1.5">
          {Object.keys(COMPONENT_LABEL).filter((k) => k in h.score_components).map((k) => [k, h.score_components[k]] as const).map(([k, v]) => (
            <li key={k}>
              <div className="flex justify-between text-[11px]"><span className={k === "contradiction" ? "text-sev-critical/90" : "text-foreground/80"}>{COMPONENT_LABEL[k] ?? k}</span><span className="tabular-nums text-dim">{v.toFixed(2)}</span></div>
              <ScoreBar value={v} className={k === "contradiction" ? "[&>div]:bg-sev-critical" : undefined} />
            </li>
          ))}
        </ul>
        <KeyValue items={[["ranking score", <span key="r" className="text-lavender">{h.ranking_score.toFixed(3)}</span>]]} />
        {can("analyst") && (
          <div className="mt-4 flex items-center gap-2">
            <span className="tm-label">was this right?</span>
            {[["correct", ThumbsUp], ["partially_correct", null], ["incorrect", ThumbsDown]].map(([v, Icon]) => (
              <button key={v as string} onClick={() => feedback(v as string)} disabled={sent !== null}
                className={cn("tm-chip hover:border-lavender disabled:opacity-50", sent === v && "border-lavender text-lavender")}>
                {Icon ? <Icon className="size-3" aria-hidden /> : null}{humanize(v as string)}
              </button>
            ))}
          </div>
        )}
      </Panel>
      <Panel label="supporting evidence" title={`${supporting.length} events`} className="xl:col-span-4" bodyClassName="max-h-[380px] overflow-y-auto">
        {supporting.length ? evidenceList(supporting, "support") : <Empty title="No supporting events linked" />}
      </Panel>
      <div className="space-y-3 xl:col-span-4">
        <Panel label="contradicting evidence" title={`${contradicting.length} events`} bodyClassName="max-h-[200px] overflow-y-auto">
          {contradicting.length ? evidenceList(contradicting, "contra") : <p className="font-sans text-[12px] text-dim">No contradicting evidence found among analysed events. This does not prove the hypothesis.</p>}
        </Panel>
        <Panel label="confidence limitations">
          <p className="font-sans text-[12px] leading-relaxed text-foreground/80">{h.uncertainty}</p>
        </Panel>
        <Panel label="suggested investigation steps · read-only">
          <ul className="space-y-1 font-sans text-[12px] text-foreground/85">
            {h.suggested_checks.map((c, i) => <li key={i} className="flex gap-2"><span className="text-lavender">›</span>{c}</li>)}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
