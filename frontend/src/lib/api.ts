// Typed client for the TraceMind REST API (same-origin /api/v1, proxied to FastAPI).

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
    public requestId?: string,
  ) {
    super(message);
  }
}

type Json = Record<string, unknown> | unknown[];

export async function api<T>(path: string, init: RequestInit & { json?: Json } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`/api/v1${path}`, {
    credentials: "same-origin",
    ...rest,
    headers: {
      Accept: "application/json",
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const err = (body as { error?: { code: string; message: string; details?: unknown; request_id?: string } })?.error;
    let message = err?.message ?? `Request failed (${res.status})`;
    // validation errors carry field-level reasons; surface the first one
    if (err?.code === "validation_error" && Array.isArray(err.details) && err.details.length) {
      const first = err.details[0] as { loc?: unknown[]; msg?: string };
      const field = first.loc?.filter((p) => typeof p === "string" && p !== "body").join(".");
      if (first.msg) message = `${field ? `${field}: ` : ""}${first.msg}`;
    }
    throw new ApiError(res.status, err?.code ?? "http_error", message, err?.details, err?.request_id);
  }
  return body as T;
}

export async function apiText(path: string): Promise<{ text: string; filename: string }> {
  const res = await fetch(`/api/v1${path}`, { credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, "http_error", `Download failed (${res.status})`);
  const disposition = res.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "download.txt";
  return { text: await res.text(), filename };
}

export function downloadText(text: string, filename: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------------------------------------------------------- types

export type Severity = "critical" | "high" | "medium" | "low";
export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface User { id: string; email: string; display_name: string; is_demo: boolean }
export interface Session { user: User; token: string }
export interface Meta { algorithm_version: string; demo_mode: boolean; llm_provider: string; llm_model: string; max_upload_bytes: number }

export interface Project {
  id: string; name: string; slug: string; description: string; is_demo: boolean; environments: string[];
  analysis_settings: Record<string, number>; created_at: string; role: "owner" | "editor" | "analyst" | "viewer" | null;
}
export interface Member { id: string; user_id: string; email: string; display_name: string; role: string; created_at: string }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export interface SeriesPoint { t: string; requests?: number; error_rate: number | null; latency_p95_ms: number | null }
export interface Series { bucket_seconds: number; overall: SeriesPoint[]; services: Record<string, SeriesPoint[]> }
export interface ServiceHealth {
  service: string; environment: string; kind: string; requests: number; error_rate: number | null;
  latency_p95_ms: number | null; open_incidents: number; status: "healthy" | "degraded" | "critical" | "no_data";
}
export interface Overview {
  project: { id: string; name: string; is_demo: boolean };
  range: { key: string; start: string | null; end: string | null; data_first: string | null; data_last: string | null };
  stats: { active_incidents: number; incidents_in_range: number; events_in_range: number; services: number; anomalies_in_range: number };
  severity_distribution: Record<Severity, number>;
  series: Series;
  service_health: ServiceHealth[];
  recent_deployments: { event_id: string; service: string; occurred_at: string; version: string | null; message: string | null }[];
  recent_investigations: {
    analysis_run_id: string; incident_id: string; incident_title: string; status: JobStatus; created_at: string;
    leading_hypothesis: string | null; writer: string | null;
  }[];
  anomaly_feed: AnomalyFeedItem[];
}
export interface AnomalyFeedItem {
  id: string; service: string; anomaly_type: string; metric: string; observed: number; baseline: number; score: number;
  severity: Severity; window_start: string; incident_id: string | null;
}

export interface Ingestion {
  id: string; project_id: string; filename: string; file_format: string; size_bytes: number; content_sha256: string;
  environment: string; service_mapping: Record<string, string>; status: JobStatus; total_rows: number; processed_rows: number;
  accepted_rows: number; rejected_rows: number; duplicate_rows: number; errors: { row: number; error: string }[];
  detection_summary: { environments?: Record<string, Record<string, number | string>> }; error_message: string | null;
  created_at: string; started_at: string | null; completed_at: string | null;
}
export interface PreviewRecord {
  row: number; service: string; environment: string; event_type: string; occurred_at: string; severity: string;
  message: string | null; trace_id: string | null; span_id: string | null; metric_name: string | null; metric_value: number | null;
  duration_ms: number | null; status_code: number | null; attributes: Record<string, unknown>; warnings: string[];
}
export interface Preview {
  format: string; sampled_rows: number; accepted_rows: number; rejected_rows: number; duplicate_rows: number;
  columns: { name: string; present_in_rows: number }[]; services: string[]; event_types: string[];
  warnings: Record<string, number>; errors: { row: number; error: string }[]; records: PreviewRecord[];
}

export interface ServiceRow {
  id: string; name: string; environment: string; kind: string; first_seen_at: string | null; last_seen_at: string | null;
  event_count: number; error_count: number; open_incidents: number;
}
export interface GraphNode {
  id: string; depth: number; kind: string; requests: number; error_rate: number | null; latency_p95_ms: number | null;
  status: ServiceHealth["status"]; open_incidents: number; affected: boolean; leading_hypothesis: boolean;
  in_blast_radius: boolean; callers: string[]; callees: string[];
}
export interface GraphEdge {
  id: string; source: string; target: string; call_count: number; error_count: number; error_rate: number | null;
  latency_p95_ms: number | null; origins: string[]; evidence_event_ids: string[];
}
export interface ServiceGraph {
  environment: string | null; incident_id: string | null; leading_service: string | null; nodes: GraphNode[];
  edges: GraphEdge[]; events_considered: number;
}

export interface EventDetail {
  id: string; service: string; environment: string; event_type: string; occurred_at: string; ingested_at: string;
  severity: string; message: string | null; trace_id: string | null; span_id: string | null; parent_span_id: string | null;
  metric_name: string | null; metric_value: number | null; duration_ms: number | null; status_code: number | null;
  attributes: Record<string, unknown>; ingestion_job_id: string | null; event_hash: string;
}

export interface Incident {
  id: string; project_id: string; title: string; status: "open" | "investigating" | "resolved"; severity: Severity;
  environment: string; affected_services: string[]; started_at: string; ended_at: string | null; created_at: string;
  latest_analysis_run_id: string | null; leading_hypothesis: string | null; leading_cause_type: string | null;
  analysis_status: JobStatus | null; anomaly_count: number;
}
export interface Anomaly {
  id: string; service: string; anomaly_type: string; metric: string; baseline: number; observed: number; score: number;
  severity: Severity; window_start: string; window_end: string; supporting_event_ids: string[]; incident_id: string | null;
  details: Record<string, unknown>;
}
export interface TimelineEntry { at: string; kind: string; service: string; title: string; event_id: string | null; anomaly_id: string | null }
export interface Evidence {
  event_id: string; polarity: "supporting" | "contradicting" | "cited"; explanation: string; occurred_at: string;
  service: string; event_type: string; severity: string; message: string | null;
}
export interface Hypothesis {
  id: string; rank: number; cause_type: string; service: string; ranking_score: number; score_components: Record<string, number>;
  summary: string; uncertainty: string; suggested_checks: string[]; evidence: Evidence[];
}
export interface AnalysisSummary {
  summary?: string;
  hypotheses?: { service: string; cause_type: string; explanation: string; supporting_evidence_ids: string[]; contradicting_evidence_ids: string[] }[];
  suggested_checks?: string[];
  uncertainties?: string[];
  suspicious_event_ids?: string[];
  meta?: { writer: "llm" | "deterministic"; model_version: string; fallback_reason?: string; citations_total?: number; citations_rejected?: number; latency_ms?: number };
}
export interface AnalysisRun {
  id: string; incident_id: string; algorithm_version: string; model_version: string; status: JobStatus; progress: number;
  stage: string; config: Record<string, unknown>; summary: AnalysisSummary; error_message: string | null; created_at: string;
  started_at: string | null; completed_at: string | null;
}
export interface IncidentMetrics extends Series {
  window: { start: string; end: string; incident_start: string; incident_end: string | null };
  deployments: { event_id: string; service: string; occurred_at: string; version: string | null }[];
}
export interface Investigation {
  id: string; question: string; answer: string; evidence: Evidence[]; uncertainties: string[]; suggested_checks: string[];
  insufficient_evidence: boolean; model_version: string; writer: string; fallback_reason: string | null;
  citations_rejected: number; created_at: string;
}
export interface Report {
  id: string; incident_id: string; analysis_run_id: string; algorithm_version: string; model_version: string;
  content_markdown: string; created_at: string;
}
export interface ReportListItem {
  id: string; incident_id: string; incident_title: string; severity: Severity; algorithm_version: string; model_version: string; created_at: string;
}

export interface StrategyMetrics {
  description: string; faults: number; top1_accuracy: number; top3_accuracy: number; detection_rate: number;
  latency_ms_mean: number; latency_ms_p95: number; citations_total: number; invalid_citation_rate: number | null;
  by_scenario_kind: Record<string, { n: number; top1: number; top3: number }>;
  by_fault_type: Record<string, { n: number; top1: number; top3: number }>;
}
export interface EvaluationRun {
  id: string; algorithm_version: string; status: JobStatus; progress: number; seed: number; scenario_count: number;
  config: { strategies?: string[] }; error_message: string | null; created_at: string; started_at: string | null; completed_at: string | null;
  metrics: {
    scenarios?: number; injected_faults?: number; anomaly_precision?: number | null; anomaly_recall?: number | null;
    anomaly_detections?: number; anomaly_truth_windows?: number; wall_clock_seconds?: number; summary_writer?: string;
    environment?: Record<string, string>; strategies?: Record<string, StrategyMetrics>;
  };
}
export interface EvaluationResult {
  scenario_id: string; strategy: string; fault_type: string; true_cause_service: string; predicted_services: string[];
  top1_correct: boolean; top3_correct: boolean; anomaly_tp: number; anomaly_fp: number; anomaly_fn: number;
  detected: boolean; multi_fault: boolean; citations_total: number; citations_invalid: number; latency_ms: number;
}
export interface StrategyInfo { name: string; description: string; weights: Record<string, number>; contradiction_weight: number; rules_only: boolean }
