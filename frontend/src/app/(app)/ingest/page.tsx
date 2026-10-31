"use client";

import { FileUp, Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Empty, ErrorState, KeyValue, Loading, Panel, StatusDot } from "@/components/common";
import { EventRef } from "@/components/event-inspector";
import { PageHeader } from "@/components/shell";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, ApiError, type EventDetail, type Ingestion, type Page, type Preview } from "@/lib/api";
import { fmtBytes, fmtDateTime, fmtNum, fmtTime, relative } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const ACCEPT = ".jsonl,.ndjson,.json,.csv,.tsv";

export default function IngestPage() {
  const { project, can, meta } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [environment, setEnvironment] = useState(project?.environments[0] ?? "production");
  const [mapping, setMapping] = useState<{ from: string; to: string }[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<"preview" | "import" | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const history = useApi<Page<Ingestion>>(project ? `/projects/${project.id}/ingestions?page_size=10` : null);
  const job = useApi<Ingestion>(jobId ? `/ingestions/${jobId}` : null, { pollMs: 800, pollWhile: (j) => !!j && (j.status === "queued" || j.status === "running") });

  const jobDone = job.data && (job.data.status === "completed" || job.data.status === "failed");
  const reloadHistory = history.reload;
  useEffect(() => {
    if (jobDone) reloadHistory();
  }, [jobDone, reloadHistory]);

  if (!project) return <Empty title="Select or create a project first" />;
  const canIngest = can("editor");
  const maxBytes = meta?.max_upload_bytes ?? 25 * 1024 * 1024;

  const choose = (f: File | null) => {
    setError(null);
    setPreview(null);
    setJobId(null);
    if (f && f.size > maxBytes) {
      setError(new ApiError(413, "payload_too_large", `File is ${fmtBytes(f.size)}; the limit is ${fmtBytes(maxBytes)}.`));
      return;
    }
    setFile(f);
  };

  const form = () => {
    const fd = new FormData();
    fd.append("file", file!);
    fd.append("environment", environment);
    const m = Object.fromEntries(mapping.filter((r) => r.from.trim() && r.to.trim()).map((r) => [r.from.trim(), r.to.trim()]));
    if (Object.keys(m).length) fd.append("service_mapping", JSON.stringify(m));
    return fd;
  };

  const doPreview = async () => {
    setBusy("preview");
    setError(null);
    try {
      setPreview(await api<Preview>(`/projects/${project.id}/ingestions/preview`, { method: "POST", body: form() }));
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(null);
    }
  };

  const doImport = async () => {
    setBusy("import");
    setError(null);
    try {
      const created = await api<Ingestion>(`/projects/${project.id}/ingestions`, {
        method: "POST", body: form(), headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      setJobId(created.id);
      toast.success(`Import queued: ${created.filename}`);
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(null);
    }
  };

  const j = job.data;
  const progress = j && j.total_rows ? Math.round((j.processed_rows / j.total_rows) * 100) : j?.status === "completed" ? 100 : 5;

  return (
    <div className="space-y-4">
      <PageHeader label="telemetry ingestion" title="Upload and normalise telemetry" />

      {!canIngest && (
        <div className="tm-panel px-3 py-2 text-[12px] text-dim">
          Your role on this project is <span className="text-white">{project.role}</span>; uploads need editor access.
          {project.is_demo && <> Create a sandbox project in <Link href="/settings" className="tm-link">Settings</Link> to try uploads.</>}
        </div>
      )}

      <div className="grid gap-3 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel label="1 · file" title="JSONL or CSV">
          <div
            onDragOver={(e) => { e.preventDefault(); if (canIngest) setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); if (canIngest) choose(e.dataTransfer.files[0] ?? null); }}
            className={cn("flex flex-col items-center justify-center gap-2 border border-dashed px-4 py-8 text-center transition-colors",
              dragging ? "border-lavender bg-accent/40" : "border-hairline", !canIngest && "opacity-50")}
          >
            <FileUp className="size-5 text-lavender" aria-hidden />
            {file ? (
              <div className="flex items-center gap-2 text-[12px]">
                <span className="text-white">{file.name}</span><span className="text-dim">{fmtBytes(file.size)}</span>
                <button aria-label="Remove file" onClick={() => choose(null)} className="text-dim hover:text-white"><X className="size-3.5" /></button>
              </div>
            ) : (
              <p className="text-[12px] text-dim">Drop a file here or</p>
            )}
            <input ref={inputRef} type="file" accept={ACCEPT} className="sr-only" id="file-input" disabled={!canIngest}
              onChange={(e) => choose(e.target.files?.[0] ?? null)} />
            <Button variant="outline" size="sm" className="text-[11px]" disabled={!canIngest} onClick={() => inputRef.current?.click()}>
              choose file
            </Button>
            <p className="text-[10px] text-dim">.jsonl .ndjson .csv .tsv · up to {fmtBytes(maxBytes)} · samples:{" "}
              <a className="tm-link" href="/samples/shopflow-sample.jsonl" download>jsonl</a>{" · "}
              <a className="tm-link" href="/samples/shopflow-sample.csv" download>csv</a>{" · "}
              <a className="tm-link" href="/samples/malformed-sample.jsonl" download>malformed</a>
            </p>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-[180px_1fr]">
            <div>
              <p className="tm-label mb-1">environment</p>
              <Select value={environment} onValueChange={setEnvironment}>
                <SelectTrigger className="h-8 text-[12px]" aria-label="Target environment"><SelectValue /></SelectTrigger>
                <SelectContent>{project.environments.map((e) => <SelectItem key={e} value={e} className="text-[12px]">{e}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between">
                <p className="tm-label">service-name mapping</p>
                <button className="tm-link flex items-center gap-1 text-[11px]" onClick={() => setMapping([...mapping, { from: "", to: "" }])}>
                  <Plus className="size-3" /> add rule
                </button>
              </div>
              {mapping.length === 0 && <p className="text-[11px] text-dim">Optional. Names are lower-cased and spaces become dashes.</p>}
              <div className="space-y-1.5">
                {mapping.map((row, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <Input aria-label="Raw service name" placeholder="raw name" value={row.from} className="h-7 text-[11px]"
                      onChange={(e) => setMapping(mapping.map((r, k) => (k === i ? { ...r, from: e.target.value } : r)))} />
                    <span className="text-dim">→</span>
                    <Input aria-label="Canonical service name" placeholder="canonical-name" value={row.to} className="h-7 text-[11px]"
                      onChange={(e) => setMapping(mapping.map((r, k) => (k === i ? { ...r, to: e.target.value } : r)))} />
                    <button aria-label="Remove rule" className="text-dim hover:text-white" onClick={() => setMapping(mapping.filter((_, k) => k !== i))}>
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled={!file || !canIngest || busy !== null} onClick={doPreview}>
              {busy === "preview" ? "validating…" : "2 · preview & validate"}
            </Button>
            <Button size="sm" disabled={!file || !canIngest || busy !== null} onClick={doImport}>
              {busy === "import" ? "uploading…" : "3 · import"}
            </Button>
          </div>
          {error && <div className="mt-3"><ErrorState error={error} /></div>}
        </Panel>

        <Panel label="import status" title={j ? j.filename : "No import running"}>
          {!j ? <Empty title="Preview a file, then import it">Rows are validated, timestamps normalised to UTC, duplicates detected and rejected rows reported with reasons.</Empty> : (
            <div className="space-y-3">
              <div className="flex items-center justify-between"><StatusDot status={j.status} /><span className="text-[11px] text-dim">{relative(j.created_at)}</span></div>
              <Progress value={progress} className="h-1" aria-label="Import progress" />
              <div className="grid grid-cols-4 gap-2 text-center">
                {[["total", j.total_rows], ["accepted", j.accepted_rows], ["rejected", j.rejected_rows], ["duplicates", j.duplicate_rows]].map(([k, v]) => (
                  <div key={k} className="border border-hairline py-2">
                    <p className={cn("text-lg tabular-nums", k === "rejected" && Number(v) > 0 ? "text-sev-high" : "text-white")}>{fmtNum(Number(v))}</p>
                    <p className="tm-label">{k}</p>
                  </div>
                ))}
              </div>
              {j.error_message && <ErrorState error={new Error(j.error_message)} />}
              {j.status === "completed" && j.detection_summary.environments && Object.entries(j.detection_summary.environments).map(([env, s]) => (
                <KeyValue key={env} items={[
                  ["environment", env], ["anomalies", String(s.anomalies)], ["incidents", `${s.incidents} (${s.incidents_created} new, ${s.incidents_updated} updated)`],
                  ["topology", String(s.topology)],
                ]} />
              ))}
              {j.status === "completed" && <Link href="/incidents" className="tm-link text-[12px]">view incidents →</Link>}
              {j.errors.length > 0 && <RowErrors errors={j.errors} total={j.rejected_rows} />}
            </div>
          )}
        </Panel>
      </div>

      {preview && <PreviewPanel preview={preview} />}

      <Panel label="ingestion history" title={`${history.data?.total ?? 0} imports`} bodyClassName="p-0">
        {history.loading && !history.data ? <div className="px-3"><Loading /></div> : history.error ? <div className="p-3"><ErrorState error={history.error} /></div> :
          !history.data?.items.length ? <Empty title="No imports yet" /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead><tr className="border-b border-hairline text-left">
                  {["file", "status", "env", "size", "rows", "accepted", "rejected", "duplicates", "created"].map((h) => <th key={h} scope="col" className="tm-label px-3 py-2 font-normal">{h}</th>)}
                </tr></thead>
                <tbody>
                  {history.data.items.map((i) => (
                    <tr key={i.id} className="cursor-pointer border-b border-hairline/60 last:border-0 hover:bg-white/[0.02]" onClick={() => setJobId(i.id)}>
                      <td className="max-w-[220px] truncate px-3 py-1.5 text-white">{i.filename}</td>
                      <td className="px-3 py-1.5"><StatusDot status={i.status} /></td>
                      <td className="px-3 py-1.5 text-dim">{i.environment}</td>
                      <td className="px-3 py-1.5 tabular-nums text-dim">{fmtBytes(i.size_bytes)}</td>
                      <td className="px-3 py-1.5 tabular-nums">{fmtNum(i.total_rows)}</td>
                      <td className="px-3 py-1.5 tabular-nums text-ok">{fmtNum(i.accepted_rows)}</td>
                      <td className={cn("px-3 py-1.5 tabular-nums", i.rejected_rows ? "text-sev-high" : "text-dim")}>{fmtNum(i.rejected_rows)}</td>
                      <td className="px-3 py-1.5 tabular-nums text-dim">{fmtNum(i.duplicate_rows)}</td>
                      <td className="px-3 py-1.5 text-dim">{fmtDateTime(i.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Panel>

      <EventBrowser projectId={project.id} />
    </div>
  );
}

function RowErrors({ errors, total }: { errors: { row: number; error: string }[]; total: number }) {
  return (
    <div>
      <p className="tm-label mb-1">rejected rows {errors.length < total && `(first ${errors.length} of ${total})`}</p>
      <ul className="max-h-40 overflow-y-auto border border-hairline text-[11px]">
        {errors.map((e, i) => (
          <li key={i} className="flex gap-3 border-b border-hairline/60 px-2 py-1 last:border-0">
            <span className="w-14 shrink-0 tabular-nums text-dim">row {e.row}</span><span className="text-sev-high">{e.error}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PreviewPanel({ preview }: { preview: Preview }) {
  return (
    <Panel label="preview · first 500 rows" title={`${preview.format.toUpperCase()} · ${preview.accepted_rows} valid, ${preview.rejected_rows} rejected, ${preview.duplicate_rows} duplicate`}>
      <div className="grid gap-4 lg:grid-cols-3">
        <div>
          <p className="tm-label mb-1">detected columns</p>
          <ul className="flex flex-wrap gap-1">{preview.columns.map((c) => <li key={c.name} className="tm-chip">{c.name} <span className="text-dim">{c.present_in_rows}</span></li>)}</ul>
          <p className="tm-label mb-1 mt-3">services after mapping</p>
          <ul className="flex flex-wrap gap-1">{preview.services.map((s) => <li key={s} className="tm-chip text-lavender">{s}</li>)}</ul>
          <p className="tm-label mb-1 mt-3">event types</p>
          <ul className="flex flex-wrap gap-1">{preview.event_types.map((s) => <li key={s} className="tm-chip">{s}</li>)}</ul>
          {Object.keys(preview.warnings).length > 0 && (
            <>
              <p className="tm-label mb-1 mt-3">warnings</p>
              <ul className="text-[11px] text-sev-medium">{Object.entries(preview.warnings).map(([w, n]) => <li key={w}>{n} × {w}</li>)}</ul>
            </>
          )}
        </div>
        <div className="lg:col-span-2">
          {preview.errors.length > 0 && <div className="mb-3"><RowErrors errors={preview.errors} total={preview.rejected_rows} /></div>}
          <p className="tm-label mb-1">normalised records</p>
          <div className="max-h-72 overflow-auto border border-hairline">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-black"><tr className="text-left">
                {["row", "time (UTC)", "service", "type", "severity", "message / metric"].map((h) => <th key={h} scope="col" className="tm-label px-2 py-1.5 font-normal">{h}</th>)}
              </tr></thead>
              <tbody>
                {preview.records.map((r) => (
                  <tr key={r.row} className="border-t border-hairline/60">
                    <td className="px-2 py-1 tabular-nums text-dim">{r.row}</td>
                    <td className="px-2 py-1 tabular-nums">{fmtTime(r.occurred_at)}</td>
                    <td className="px-2 py-1 text-lavender">{r.service}</td>
                    <td className="px-2 py-1">{r.event_type}</td>
                    <td className="px-2 py-1">{r.severity}</td>
                    <td className="max-w-[320px] truncate px-2 py-1 text-foreground/80">
                      {r.message ?? (r.metric_name ? `${r.metric_name}=${r.metric_value}` : r.duration_ms !== null ? `${r.duration_ms}ms` : "")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Panel>
  );
}

function EventBrowser({ projectId }: { projectId: string }) {
  const [filters, setFilters] = useState({ service: "", event_type: "", severity: "", q: "" });
  const [page, setPage] = useState(1);
  const qs = new URLSearchParams({ page: String(page), page_size: "25", ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) });
  const { data, error, loading } = useApi<Page<EventDetail>>(`/projects/${projectId}/events?${qs}`);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const set = (k: keyof typeof filters, v: string) => { setFilters({ ...filters, [k]: v }); setPage(1); };
  return (
    <Panel label="normalised telemetry" title={`${fmtNum(data?.total ?? 0)} stored events`} bodyClassName="p-0">
      <div className="flex flex-wrap gap-2 border-b border-hairline p-3">
        <Input aria-label="Filter by service" placeholder="service" className="h-7 w-36 text-[11px]" value={filters.service} onChange={(e) => set("service", e.target.value)} />
        <Select value={filters.event_type || "__any"} onValueChange={(v) => set("event_type", v === "__any" ? "" : v)}>
          <SelectTrigger className="h-7 w-28 text-[11px]" aria-label="Filter by type"><SelectValue /></SelectTrigger>
          <SelectContent>{["__any", "log", "span", "metric", "deployment", "dependency"].map((t) => <SelectItem key={t} value={t} className="text-[11px]">{t === "__any" ? "any type" : t}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={filters.severity || "__any"} onValueChange={(v) => set("severity", v === "__any" ? "" : v)}>
          <SelectTrigger className="h-7 w-28 text-[11px]" aria-label="Filter by severity"><SelectValue /></SelectTrigger>
          <SelectContent>{["__any", "debug", "info", "warning", "error", "critical"].map((t) => <SelectItem key={t} value={t} className="text-[11px]">{t === "__any" ? "any severity" : t}</SelectItem>)}</SelectContent>
        </Select>
        <Input aria-label="Search messages" placeholder="search message" className="h-7 w-48 text-[11px]" value={filters.q} onChange={(e) => set("q", e.target.value)} />
      </div>
      {loading && !data ? <div className="px-3"><Loading /></div> : error ? <div className="p-3"><ErrorState error={error} /></div> : !data?.items.length ? <Empty title="No events match" /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead><tr className="border-b border-hairline text-left">
              {["time (UTC)", "service", "type", "severity", "trace", "message / value", ""].map((h) => <th key={h} scope="col" className="tm-label px-3 py-2 font-normal">{h}</th>)}
            </tr></thead>
            <tbody>
              {data.items.map((e) => (
                <tr key={e.id} className="border-b border-hairline/60 last:border-0">
                  <td className="whitespace-nowrap px-3 py-1 tabular-nums">{fmtDateTime(e.occurred_at).slice(5, 19)}</td>
                  <td className="px-3 py-1 text-lavender">{e.service}</td>
                  <td className="px-3 py-1">{e.event_type}</td>
                  <td className={cn("px-3 py-1", ["error", "critical", "fatal"].includes(e.severity) && "text-sev-critical")}>{e.severity}</td>
                  <td className="px-3 py-1 text-dim">{e.trace_id?.slice(0, 8) ?? "—"}</td>
                  <td className="max-w-[380px] truncate px-3 py-1 text-foreground/80">
                    {e.message ?? (e.metric_name ? `${e.metric_name}=${e.metric_value}` : e.duration_ms !== null ? `${e.duration_ms.toFixed(1)}ms · ${e.status_code}` : "")}
                  </td>
                  <td className="px-3 py-1 text-right"><EventRef id={e.id}>open</EventRef></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex items-center justify-end gap-2 border-t border-hairline px-3 py-2 text-[11px]">
        <Button variant="ghost" size="sm" className="h-6 text-[11px]" disabled={page <= 1} onClick={() => setPage(page - 1)}>prev</Button>
        <span className="tabular-nums text-dim">{page} / {pages}</span>
        <Button variant="ghost" size="sm" className="h-6 text-[11px]" disabled={page >= pages} onClick={() => setPage(page + 1)}>next</Button>
      </div>
    </Panel>
  );
}
