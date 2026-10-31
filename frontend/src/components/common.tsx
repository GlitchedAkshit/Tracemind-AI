"use client";

import { AlertTriangle, Inbox, Loader2 } from "lucide-react";

import type { ApiError, Severity } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Panel({ title, label, actions, className, children, bodyClassName }: {
  title?: string; label?: string; actions?: React.ReactNode; className?: string; children: React.ReactNode; bodyClassName?: string;
}) {
  return (
    <section className={cn("tm-panel flex min-w-0 flex-col", className)} aria-label={[label, title].filter(Boolean).join(": ") || undefined}>
      {(title || label || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-hairline px-3 py-2">
          <div className="min-w-0">
            {label && <p className="tm-label">{label}</p>}
            {title && <h2 className="truncate text-[12px] text-white">{title}</h2>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn("min-w-0 flex-1 p-3", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, accent }: { label: string; value: React.ReactNode; hint?: string; accent?: boolean }) {
  return (
    <div className="tm-panel px-3 py-2.5">
      <p className="tm-label">{label}</p>
      <p className={cn("mt-1 text-xl tabular-nums", accent ? "text-lavender" : "text-white")}>{value}</p>
      {hint && <p className="mt-0.5 text-[10px] text-dim">{hint}</p>}
    </div>
  );
}

const SEV_CLASS: Record<Severity, string> = {
  critical: "border-sev-critical/50 text-sev-critical",
  high: "border-sev-high/50 text-sev-high",
  medium: "border-sev-medium/50 text-sev-medium",
  low: "border-sev-low/50 text-sev-low",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={cn("tm-chip uppercase tracking-wider", SEV_CLASS[severity])}>{severity}</span>;
}

const STATUS_CLASS: Record<string, string> = {
  healthy: "bg-ok", degraded: "bg-sev-medium", critical: "bg-sev-critical", no_data: "bg-dim/50",
  completed: "bg-ok", running: "bg-lavender animate-pulse", queued: "bg-dim", failed: "bg-sev-critical",
  open: "bg-sev-critical", investigating: "bg-lavender", resolved: "bg-ok",
};

export function StatusDot({ status, label }: { status: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-foreground/85">
      <span className={cn("size-1.5 rounded-full", STATUS_CLASS[status] ?? "bg-dim")} aria-hidden />
      {label ?? status.replace("_", " ")}
    </span>
  );
}

export function Loading({ label = "loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-dim" role="status" aria-live="polite">
      <Loader2 className="size-3.5 animate-spin text-lavender" aria-hidden />
      <span className="tm-label">{label}…</span>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      <Inbox className="size-4 text-dim" aria-hidden />
      <p className="text-[12px] text-foreground/80">{title}</p>
      {children && <div className="max-w-sm text-[11px] text-dim">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: ApiError | Error; retry?: () => void }) {
  const requestId = "requestId" in error ? (error as ApiError).requestId : undefined;
  return (
    <div className="flex items-start gap-2 border border-sev-critical/40 bg-sev-critical/5 p-3 text-[12px]" role="alert">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-sev-critical" aria-hidden />
      <div className="min-w-0">
        <p className="text-white">{error.message}</p>
        {requestId && <p className="mt-0.5 text-[10px] text-dim">request {requestId}</p>}
        {retry && <button onClick={retry} className="tm-link mt-1 text-[11px]">retry</button>}
      </div>
    </div>
  );
}

export function KeyValue({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[12px]">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="tm-label pt-0.5">{k}</dt>
          <dd className="min-w-0 break-words text-foreground/90">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ScoreBar({ value, max = 1, className }: { value: number; max?: number; className?: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className={cn("h-1 w-full bg-chip", className)} role="presentation">
      <div className="h-full bg-lavender transition-[width] duration-500" style={{ width: `${pct}%` }} />
    </div>
  );
}
