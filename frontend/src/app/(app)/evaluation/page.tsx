"use client";

import { Download, Play } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import { Empty, ErrorState, KeyValue, Loading, Panel, Stat, StatusDot } from "@/components/common";
import { PageHeader } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { api, ApiError, apiText, downloadText, type EvaluationResult, type EvaluationRun, type StrategyInfo } from "@/lib/api";
import { fmtDateTime, fmtMs, fmtPct, humanize } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export default function EvaluationPage() {
  const runs = useApi<EvaluationRun[]>("/evaluation-runs", { pollMs: 2000, pollWhile: (r) => !!r?.some((x) => x.status === "queued" || x.status === "running") });
  const strategies = useApi<StrategyInfo[]>("/evaluation/strategies");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [seed, setSeed] = useState(42);
  const [count, setCount] = useState(20);
  const [busy, setBusy] = useState(false);


  const start = async () => {
    setBusy(true);
    try {
      const run = await api<EvaluationRun>("/evaluation-runs", { method: "POST", json: { seed, scenario_count: count } });
      setSelectedId(run.id);
      runs.reload();
      toast.success(`Benchmark started: ${count} scenarios, seed ${seed}`);
    } catch (e) {
      toast.error((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const defaultId = (runs.data?.find((r) => r.status === "completed") ?? runs.data?.[0])?.id ?? null;
  const run = runs.data?.find((r) => r.id === (selectedId ?? defaultId));

  return (
    <div className="space-y-4">
      <PageHeader label="evaluation laboratory" title="Benchmarks on synthetic fault scenarios with known causes" />
      <div className="grid gap-3 xl:grid-cols-[320px_1fr]">
        <div className="space-y-3">
          <Panel label="new benchmark run">
            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1"><span className="tm-label">seed</span>
                <Input type="number" min={0} max={1000000} value={seed} onChange={(e) => setSeed(Number(e.target.value))} className="h-8 text-[12px]" /></label>
              <label className="space-y-1"><span className="tm-label">scenarios (≤60)</span>
                <Input type="number" min={1} max={60} value={count} onChange={(e) => setCount(Number(e.target.value))} className="h-8 text-[12px]" /></label>
            </div>
            <p className="mt-2 font-sans text-[11px] leading-relaxed text-dim">
              Each scenario simulates 60 min of ShopFlow traffic with an injected fault (bad deployment, latency injection,
              resource exhaustion, dependency outage), noise blips, decoy deployments and missing trace IDs. ~20% contain two concurrent faults.
            </p>
            <Button size="sm" className="mt-3 w-full" onClick={start} disabled={busy || count < 1 || count > 60}><Play className="size-3.5" /> run all strategies</Button>
          </Panel>
          <Panel label="runs" bodyClassName="p-0">
            {runs.loading && !runs.data ? <div className="px-3"><Loading /></div> : !runs.data?.length ? <Empty title="No benchmark runs yet" /> : (
              <ul>
                {runs.data.map((r) => (
                  <li key={r.id}>
                    <button onClick={() => setSelectedId(r.id)} className={cn("w-full border-b border-l-2 border-b-hairline/60 px-3 py-2 text-left",
                      r.id === run?.id ? "border-l-lavender bg-accent/50" : "border-l-transparent hover:bg-white/[0.02]")}>
                      <div className="flex justify-between text-[12px]"><span className="text-white">seed {r.seed} · n={r.scenario_count}</span><StatusDot status={r.status} /></div>
                      <p className="text-[10px] text-dim">{fmtDateTime(r.created_at)} · {r.algorithm_version}</p>
                      {(r.status === "running" || r.status === "queued") && <Progress value={r.progress} className="mt-1 h-0.5" />}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
        <div className="min-w-0 space-y-3">
          {runs.error && <ErrorState error={runs.error} />}
          {!run ? <Panel><Empty title="Select or start a run">Results are computed from recorded runs only — nothing here is invented.</Empty></Panel> :
            run.status !== "completed" ? (
              <Panel label={`run ${run.id.slice(0, 8)}`}>
                {run.status === "failed" ? <ErrorState error={new Error(run.error_message ?? "Evaluation failed")} /> : (
                  <div className="space-y-2"><p className="text-[12px]">{run.status}… {run.progress}%</p><Progress value={run.progress} className="h-1" /></div>
                )}
              </Panel>
            ) : <RunResults run={run} strategies={strategies.data ?? []} />}
        </div>
      </div>
    </div>
  );
}

function RunResults({ run, strategies }: { run: EvaluationRun; strategies: StrategyInfo[] }) {
  const m = run.metrics;
  const rows = Object.entries(m.strategies ?? {});
  const hybrid = m.strategies?.hybrid;
  const results = useApi<EvaluationResult[]>(`/evaluation-runs/${run.id}/results?strategy=hybrid`);
  const chart = rows.map(([name, s]) => ({ name: humanize(name), top1: +(s.top1_accuracy * 100).toFixed(1), top3: +(s.top3_accuracy * 100).toFixed(1) }));
  const download = async (format: "json" | "csv") => {
    const { text, filename } = await apiText(`/evaluation-runs/${run.id}/export?format=${format}`);
    downloadText(text, filename, format === "csv" ? "text/csv" : "application/json");
  };
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
        <Stat label="scenarios" value={m.scenarios ?? "—"} hint={`${m.injected_faults} injected faults`} />
        <Stat label="top-1 (hybrid)" value={fmtPct(hybrid?.top1_accuracy)} accent />
        <Stat label="top-3 (hybrid)" value={fmtPct(hybrid?.top3_accuracy)} />
        <Stat label="anomaly precision" value={fmtPct(m.anomaly_precision)} hint={`${m.anomaly_detections} detections`} />
        <Stat label="anomaly recall" value={fmtPct(m.anomaly_recall)} hint={`${m.anomaly_truth_windows} truth windows`} />
        <Stat label="invalid citations" value={fmtPct(hybrid?.invalid_citation_rate ?? null)} hint={`${hybrid?.citations_total ?? 0} checked`} />
      </div>

      <Panel label="root-cause accuracy by configuration" title="Baseline, ablations and the full hybrid pipeline"
        actions={<div className="flex gap-1">
          <Button variant="outline" size="sm" className="h-6 text-[10px]" onClick={() => download("json")}><Download className="size-3" /> json</Button>
          <Button variant="outline" size="sm" className="h-6 text-[10px]" onClick={() => download("csv")}><Download className="size-3" /> csv</Button>
        </div>}>
        <div className="h-[220px]" role="img" aria-label="Top-1 and top-3 accuracy by strategy">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#161616" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: "#8a8a8a", fontSize: 9 }} stroke="#2a2a2a" interval={0} angle={-12} height={44} textAnchor="end" />
              <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fill: "#8a8a8a", fontSize: 10 }} stroke="#2a2a2a" width={40} />
              <Tooltip contentStyle={{ background: "#0b0b0b", border: "1px solid #2a2a2a", fontSize: 11 }} formatter={(v) => `${v}%`} cursor={{ fill: "rgba(170,136,255,0.06)" }} />
              <Bar dataKey="top1" name="top-1" fill="#aa88ff" isAnimationActive={false} />
              <Bar dataKey="top3" name="top-3" fill="#3d3354" isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead><tr className="border-b border-hairline text-left">
              {["configuration", "top-1", "top-3", "single-fault top-1", "multi-fault top-1", "detected", "latency mean", "latency p95"].map((h) => <th key={h} scope="col" className="tm-label px-2 py-1.5 font-normal">{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map(([name, s]) => (
                <tr key={name} className={cn("border-b border-hairline/60", name === "hybrid" && "bg-accent/40")}>
                  <td className="px-2 py-1.5"><span className="text-white">{humanize(name)}</span>
                    <p className="max-w-[280px] font-sans text-[10px] text-dim">{s.description}</p></td>
                  <td className="px-2 py-1.5 tabular-nums text-lavender">{fmtPct(s.top1_accuracy)}</td>
                  <td className="px-2 py-1.5 tabular-nums">{fmtPct(s.top3_accuracy)}</td>
                  <td className="px-2 py-1.5 tabular-nums">{fmtPct(s.by_scenario_kind.single_fault?.top1)} <span className="text-dim">n={s.by_scenario_kind.single_fault?.n ?? 0}</span></td>
                  <td className="px-2 py-1.5 tabular-nums">{fmtPct(s.by_scenario_kind.multi_fault?.top1)} <span className="text-dim">n={s.by_scenario_kind.multi_fault?.n ?? 0}</span></td>
                  <td className="px-2 py-1.5 tabular-nums">{fmtPct(s.detection_rate)}</td>
                  <td className="px-2 py-1.5 tabular-nums">{fmtMs(s.latency_ms_mean)}</td>
                  <td className="px-2 py-1.5 tabular-nums">{fmtMs(s.latency_ms_p95)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel label="hybrid by fault type">
          <table className="w-full text-[12px]">
            <thead><tr className="text-left">{["fault type", "n", "top-1", "top-3"].map((h) => <th key={h} scope="col" className="tm-label px-2 py-1 font-normal">{h}</th>)}</tr></thead>
            <tbody>{Object.entries(hybrid?.by_fault_type ?? {}).map(([k, v]) => (
              <tr key={k} className="border-t border-hairline/60"><td className="px-2 py-1">{humanize(k)}</td><td className="px-2 py-1 text-dim">{v.n}</td>
                <td className="px-2 py-1 tabular-nums text-lavender">{fmtPct(v.top1)}</td><td className="px-2 py-1 tabular-nums">{fmtPct(v.top3)}</td></tr>
            ))}</tbody>
          </table>
        </Panel>
        <Panel label="provenance">
          <KeyValue items={[
            ["algorithm", run.algorithm_version], ["seed", String(run.seed)], ["summary writer", m.summary_writer ?? "—"],
            ["wall clock", `${m.wall_clock_seconds}s`], ["machine", `${m.environment?.system ?? ""} ${m.environment?.machine ?? ""} · python ${m.environment?.python ?? ""}`],
            ["completed", fmtDateTime(run.completed_at)],
          ]} />
          <p className="mt-3 font-sans text-[11px] leading-relaxed text-dim">
            Latency is wall-clock time for detection plus ranking (and summary for hybrid) on the API host. Synthetic scenarios are
            cleaner than production telemetry, so these numbers are an upper bound on real-world accuracy, not a promise.
          </p>
        </Panel>
      </div>

      <Panel label="per-scenario results · hybrid" bodyClassName="max-h-[380px] overflow-auto p-0">
        {results.loading ? <div className="px-3"><Loading /></div> : (
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-black"><tr className="text-left">{["scenario", "fault", "true cause", "predicted (top 3)", "top-1", "top-3", "anomaly tp/fp/fn"].map((h) => <th key={h} scope="col" className="tm-label px-2 py-1.5 font-normal">{h}</th>)}</tr></thead>
            <tbody>{results.data?.map((r, i) => (
              <tr key={i} className="border-t border-hairline/60">
                <td className="px-2 py-1 text-dim">{r.scenario_id}{r.multi_fault && " · multi"}</td><td className="px-2 py-1">{humanize(r.fault_type)}</td>
                <td className="px-2 py-1 text-white">{r.true_cause_service}</td>
                <td className="px-2 py-1">{r.predicted_services.slice(0, 3).map((s, k) => <span key={s} className={cn(s === r.true_cause_service ? "text-lavender" : "text-dim")}>{k ? ", " : ""}{s}</span>)}</td>
                <td className={cn("px-2 py-1", r.top1_correct ? "text-ok" : "text-sev-critical")}>{r.top1_correct ? "✓" : "✗"}</td>
                <td className={cn("px-2 py-1", r.top3_correct ? "text-ok" : "text-sev-critical")}>{r.top3_correct ? "✓" : "✗"}</td>
                <td className="px-2 py-1 tabular-nums text-dim">{r.anomaly_tp}/{r.anomaly_fp}/{r.anomaly_fn}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Panel>

      {strategies.length > 0 && (
        <Panel label="strategy definitions" title="Weights used by each configuration">
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
            {strategies.map((s) => (
              <div key={s.name} className="border border-hairline p-2 text-[11px]">
                <p className="text-white">{humanize(s.name)}</p>
                {s.rules_only ? <p className="text-dim">rule-based, no weights</p> : (
                  <ul className="mt-1 text-dim">{Object.entries(s.weights).map(([k, v]) => <li key={k}>{k} {v}</li>)}<li>contradiction −{s.contradiction_weight}</li></ul>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}
