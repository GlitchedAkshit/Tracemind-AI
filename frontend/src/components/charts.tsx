"use client";

import { Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { SeriesPoint } from "@/lib/api";
import { fmtMs, fmtPct, fmtTime } from "@/lib/format";

const AXIS = { stroke: "#2a2a2a", tick: { fill: "#8a8a8a", fontSize: 10, fontFamily: "var(--font-mono-stack)" }, tickLine: false };
const PALETTE = ["#aa88ff", "#6fd6a8", "#ffa45c", "#7fb8ff", "#ff6b81", "#e6cf5c", "#c9b3ff", "#5cc8e6", "#f29fd0", "#a3a3a3"];
const tooltipStyle = {
  contentStyle: { background: "#0b0b0b", border: "1px solid #2a2a2a", borderRadius: 2, fontSize: 11, fontFamily: "var(--font-mono-stack)" },
  labelStyle: { color: "#8a8a8a" },
};

export type Metric = "error_rate" | "latency_p95_ms";
const fmt = (metric: Metric) => (v: number) => (metric === "error_rate" ? fmtPct(v, 0) : fmtMs(v));

export function OverallChart({ data, metric, height = 180 }: { data: SeriesPoint[]; metric: Metric; height?: number }) {
  const label = metric === "error_rate" ? "error rate" : "p95 latency";
  return (
    <div style={{ height }} role="img" aria-label={`${label} over time`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={`fill-${metric}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#aa88ff" stopOpacity={0.28} />
              <stop offset="100%" stopColor="#aa88ff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#161616" vertical={false} />
          <XAxis dataKey="t" tickFormatter={(t) => fmtTime(t).slice(0, 5)} minTickGap={40} {...AXIS} />
          <YAxis tickFormatter={fmt(metric)} width={48} {...AXIS} />
          <Tooltip {...tooltipStyle} labelFormatter={(t) => fmtTime(String(t), true)} formatter={(v) => [fmt(metric)(Number(v)), label]} />
          <Area type="monotone" dataKey={metric} stroke="#aa88ff" strokeWidth={1.25} fill={`url(#fill-${metric})`} connectNulls isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ServiceChart({ series, metric, height = 220, markers = [], window }: {
  series: Record<string, SeriesPoint[]>; metric: Metric; height?: number;
  markers?: { t: string; label: string }[]; window?: { start: string; end: string | null };
}) {
  const services = Object.keys(series);
  const times = new Map<string, Record<string, number | string | null>>();
  for (const s of services) {
    for (const p of series[s]) {
      const row = times.get(p.t) ?? { t: p.t };
      row[s] = p[metric];
      times.set(p.t, row);
    }
  }
  const data = [...times.values()].sort((a, b) => String(a.t).localeCompare(String(b.t)));
  // snap markers onto the chart's bucket timestamps so they render on a categorical axis
  const snap = (iso: string) => {
    const target = new Date(iso).getTime();
    let best = data[0]?.t as string | undefined;
    for (const row of data) if (new Date(String(row.t)).getTime() <= target) best = String(row.t);
    return best;
  };
  const label = metric === "error_rate" ? "error rate" : "p95 latency";
  return (
    <div style={{ height }} role="img" aria-label={`${label} by service`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#161616" vertical={false} />
          <XAxis dataKey="t" tickFormatter={(t) => fmtTime(t).slice(0, 5)} minTickGap={40} {...AXIS} />
          <YAxis tickFormatter={fmt(metric)} width={48} {...AXIS} />
          <Tooltip {...tooltipStyle} labelFormatter={(t) => fmtTime(String(t), true)} formatter={(v, name) => [v === null ? "—" : fmt(metric)(Number(v)), name]} />
          {window && data.length > 0 && (
            <ReferenceArea x1={snap(window.start)} x2={snap(window.end ?? window.start)} fill="#aa88ff" fillOpacity={0.06} stroke="none" />
          )}
          {markers.map((m) => (
            <ReferenceLine key={m.t + m.label} x={snap(m.t)} stroke="#ffa45c" strokeDasharray="2 3"
              label={{ value: m.label, fill: "#ffa45c", fontSize: 9, position: "insideTopLeft" }} />
          ))}
          {services.map((s, i) => (
            <Line key={s} type="monotone" dataKey={s} stroke={PALETTE[i % PALETTE.length]} strokeWidth={1.1} dot={false} connectNulls isAnimationActive={false} />
          ))}
          <Legend wrapperStyle={{ fontSize: 10, fontFamily: "var(--font-mono-stack)", color: "#8a8a8a" }} iconSize={8} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
