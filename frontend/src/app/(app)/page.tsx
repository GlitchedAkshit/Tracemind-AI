"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";

import { OverallChart } from "@/components/charts";
import { Empty, ErrorState, Loading, Panel, SeverityBadge, Stat, StatusDot } from "@/components/common";
import { EventRef } from "@/components/event-inspector";
import { ParticleField } from "@/components/particles/ParticleField";
import { useSession } from "@/components/session";
import type { Overview } from "@/lib/api";
import { fmtDateTime, fmtMs, fmtNum, fmtPct, fmtTime, humanize, relative, SEVERITY_ORDER } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export default function OverviewPage() {
  const { project, environment, range } = useSession();
  const qs = new URLSearchParams({ range, ...(environment ? { environment } : {}) });
  const { data, error, loading, reload } = useApi<Overview>(project ? `/projects/${project.id}/overview?${qs}` : null, { pollMs: 30000 });

  if (!project) return <Empty title="No project yet">Create a project in <Link className="tm-link" href="/settings">Settings</Link>, then upload telemetry.</Empty>;

  const sev = data?.severity_distribution;
  const sevTotal = sev ? SEVERITY_ORDER.reduce((n, s) => n + sev[s], 0) : 0;

  return (
    <div className="space-y-4">
      <section className="tm-panel relative h-[300px] overflow-hidden sm:h-[320px]" aria-label="Overview hero">
        <ParticleField className="absolute inset-y-0 right-0 w-full md:w-[68%]" label="Particle ring visual" />
        <div className="absolute inset-0 bg-gradient-to-r from-black via-black/80 to-transparent md:via-black/40" aria-hidden />
        <div className="relative flex h-full max-w-xl flex-col justify-between p-5 sm:p-7">
          <div>
            <p className="tm-label">overview · {project.name}</p>
            {project.is_demo && (
              <p className="mt-2 inline-block border border-lavender/40 px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] text-lavender">
                synthetic demo data
              </p>
            )}
          </div>
          <div>
            <p className="text-[44px] leading-none tabular-nums text-white sm:text-[56px]">
              {data ? data.stats.active_incidents : "—"}
            </p>
            <p className="mt-2 text-[12px] tracking-wide text-foreground/80">active incidents under investigation</p>
            <p className="mt-3 max-w-md font-sans text-[13px] leading-relaxed text-dim">
              Anomalies are detected with robust baselines, grouped by time and topology, and ranked by an interpretable
              model. Every hypothesis links back to the telemetry behind it.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/incidents" className="tm-chip border-lavender/40 text-lavender hover:bg-accent">open incident explorer <ArrowUpRight className="size-3" /></Link>
            <Link href="/ingest" className="tm-chip hover:border-dim">upload telemetry</Link>
          </div>
        </div>
      </section>

      {loading && !data && <Loading label="loading overview" />}
      {error && <ErrorState error={error} retry={reload} />}

      {data && (
        <>
          {data.stats.events_in_range === 0 && (
            <div className="tm-panel flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[12px]">
              <span className="text-dim">No telemetry in the selected range.{data.range.data_last && ` Latest data: ${fmtDateTime(data.range.data_last)}.`}</span>
              <span className="tm-label">choose “all data” in the range selector</span>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5">
            <Stat label="events in range" value={fmtNum(data.stats.events_in_range)} />
            <Stat label="incidents in range" value={data.stats.incidents_in_range} />
            <Stat label="anomalies" value={data.stats.anomalies_in_range} />
            <Stat label="services" value={data.stats.services} />
            <div className="tm-panel col-span-2 px-3 py-2.5 md:col-span-4 xl:col-span-1">
              <p className="tm-label">active by severity</p>
              <div className="mt-2 flex h-1.5 overflow-hidden bg-chip" aria-hidden>
                {sevTotal > 0 && SEVERITY_ORDER.map((s) => (
                  <div key={s} className={cn({ critical: "bg-sev-critical", high: "bg-sev-high", medium: "bg-sev-medium", low: "bg-sev-low" }[s])}
                    style={{ width: `${(sev![s] / sevTotal) * 100}%` }} />
                ))}
              </div>
              <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-dim">
                {SEVERITY_ORDER.map((s) => <li key={s} className="whitespace-nowrap"><span className="text-white tabular-nums">{sev![s]}</span> {s}</li>)}
              </ul>
            </div>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel label="error rate" title="All services, aggregated">
              {data.series.overall.length ? <OverallChart data={data.series.overall} metric="error_rate" /> : <Empty title="No request data in range" />}
            </Panel>
            <Panel label="latency" title="p95 span duration">
              {data.series.overall.length ? <OverallChart data={data.series.overall} metric="latency_p95_ms" /> : <Empty title="No span data in range" />}
            </Panel>
          </div>

          <div className="grid gap-3 xl:grid-cols-3">
            <Panel label="service health" title={`${data.service_health.length} services`} className="xl:col-span-2" bodyClassName="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-hairline text-left">
                      {["service", "status", "requests", "error rate", "p95", "open incidents"].map((h) => (
                        <th key={h} scope="col" className="tm-label px-3 py-2 font-normal">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.service_health.map((s) => (
                      <tr key={s.service + s.environment} className="border-b border-hairline/60 last:border-0 hover:bg-white/[0.02]">
                        <td className="px-3 py-1.5 text-white">{s.service}</td>
                        <td className="px-3 py-1.5"><StatusDot status={s.status} /></td>
                        <td className="px-3 py-1.5 tabular-nums text-foreground/80">{fmtNum(s.requests)}</td>
                        <td className={cn("px-3 py-1.5 tabular-nums", (s.error_rate ?? 0) >= 0.05 ? "text-sev-critical" : "text-foreground/80")}>{fmtPct(s.error_rate, 2)}</td>
                        <td className="px-3 py-1.5 tabular-nums text-foreground/80">{fmtMs(s.latency_p95_ms)}</td>
                        <td className="px-3 py-1.5 tabular-nums">{s.open_incidents || <span className="text-dim">0</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            <Panel label="anomaly feed" title="Most recent detections" bodyClassName="max-h-[420px] overflow-y-auto p-0">
              {data.anomaly_feed.length === 0 ? <Empty title="No anomalies in range" /> : (
                <ul>
                  {data.anomaly_feed.map((a) => (
                    <li key={a.id} className="border-b border-hairline/60 px-3 py-2 last:border-0">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-[12px] text-white">{a.service}</span>
                        <SeverityBadge severity={a.severity} />
                      </div>
                      <p className="mt-0.5 text-[11px] text-foreground/80">{humanize(a.anomaly_type)} · {a.metric}</p>
                      <div className="mt-0.5 flex items-center justify-between text-[10px] text-dim">
                        <span>{fmtTime(a.window_start)} UTC · obs {a.observed.toPrecision(3)} vs {a.baseline.toPrecision(3)}</span>
                        {a.incident_id && <Link href={`/incidents/${a.incident_id}`} className="tm-link">incident</Link>}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel label="recent deployments" title="Changes in range" bodyClassName="p-0">
              {data.recent_deployments.length === 0 ? <Empty title="No deployments recorded in range" /> : (
                <ul>
                  {data.recent_deployments.map((d) => (
                    <li key={d.event_id} className="flex items-center justify-between gap-2 border-b border-hairline/60 px-3 py-2 last:border-0">
                      <div className="min-w-0">
                        <p className="truncate text-[12px] text-white">{d.service} <span className="text-lavender">{d.version}</span></p>
                        <p className="text-[10px] text-dim">{fmtDateTime(d.occurred_at)}</p>
                      </div>
                      <EventRef id={d.event_id}>event</EventRef>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel label="recent investigations" title="Analysis runs" bodyClassName="p-0">
              {data.recent_investigations.length === 0 ? <Empty title="No analyses yet">Open an incident and run an analysis.</Empty> : (
                <ul>
                  {data.recent_investigations.map((r) => (
                    <li key={r.analysis_run_id} className="border-b border-hairline/60 px-3 py-2 last:border-0">
                      <Link href={`/incidents/${r.incident_id}`} className="block truncate text-[12px] text-white hover:text-lavender">{r.incident_title}</Link>
                      <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-dim">
                        <StatusDot status={r.status} />
                        <span>lead: <span className="text-foreground/90">{r.leading_hypothesis ?? "—"}</span></span>
                        <span>writer: {r.writer ?? "—"}</span>
                        <span>{relative(r.created_at)}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
