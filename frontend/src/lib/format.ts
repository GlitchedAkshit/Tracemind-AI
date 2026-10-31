import type { Severity } from "./api";

export const fmtTime = (iso: string | null | undefined, withDate = false) => {
  if (!iso) return "—";
  const d = new Date(iso);
  const time = d.toISOString().slice(11, 19);
  return withDate ? `${d.toISOString().slice(0, 10)} ${time}` : time;
};

export const fmtDateTime = (iso: string | null | undefined) => fmtTime(iso, true) + (iso ? " UTC" : "");

export const fmtPct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`);

export const fmtMs = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`;

export const fmtNum = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString("en-US"));

export const fmtBytes = (v: number) => (v > 1024 * 1024 ? `${(v / 1024 / 1024).toFixed(1)} MB` : `${(v / 1024).toFixed(1)} KB`);

export const relative = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export const duration = (start: string, end: string | null) => {
  if (!end) return "ongoing";
  const m = (new Date(end).getTime() - new Date(start).getTime()) / 60000;
  return m < 60 ? `${Math.round(m)} min` : `${(m / 60).toFixed(1)} h`;
};

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low"];

export const humanize = (s: string) => s.replace(/_/g, " ");

export const CAUSE_LABEL: Record<string, string> = {
  deployment_regression: "Deployment regression",
  resource_saturation: "Resource saturation",
  latency_degradation: "Latency degradation",
  error_spike: "Elevated failure rate",
  traffic_anomaly: "Abnormal traffic",
};
