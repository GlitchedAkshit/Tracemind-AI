"use client";

import { createContext, useCallback, useContext, useState } from "react";

import { ErrorState, KeyValue, Loading } from "@/components/common";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { EventDetail } from "@/lib/api";
import { fmtDateTime, fmtMs } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const Ctx = createContext<(eventId: string) => void>(() => undefined);

export function useEventInspector() {
  return useContext(Ctx);
}

export function EventInspectorProvider({ children }: { children: React.ReactNode }) {
  const [eventId, setEventId] = useState<string | null>(null);
  const open = useCallback((id: string) => setEventId(id), []);
  return (
    <Ctx.Provider value={open}>
      {children}
      <Sheet open={eventId !== null} onOpenChange={(o) => !o && setEventId(null)}>
        <SheetContent side="right" className="w-full overflow-y-auto border-hairline bg-[#050505] sm:max-w-lg">
          <SheetHeader>
            <SheetTitle className="text-[12px] font-normal tracking-wide text-white">Telemetry event</SheetTitle>
            <SheetDescription className="font-mono text-[10px] text-dim">{eventId}</SheetDescription>
          </SheetHeader>
          {eventId && <EventBody id={eventId} />}
        </SheetContent>
      </Sheet>
    </Ctx.Provider>
  );
}

function EventBody({ id }: { id: string }) {
  const { data, error, loading, reload } = useApi<EventDetail>(`/events/${id}`);
  if (loading) return <div className="px-4"><Loading label="loading event" /></div>;
  if (error) return <div className="px-4"><ErrorState error={error} retry={reload} /></div>;
  if (!data) return null;
  return (
    <div className="space-y-5 px-4 pb-6">
      <p className="tm-label">observed telemetry · not an inference</p>
      <KeyValue items={[
        ["timestamp", fmtDateTime(data.occurred_at)],
        ["service", <span key="s" className="text-lavender">{data.service}</span>],
        ["environment", data.environment],
        ["event type", data.event_type],
        ["severity", <span key="sev" className={cn(["error", "critical", "fatal"].includes(data.severity) && "text-sev-critical")}>{data.severity}</span>],
        ...(data.status_code !== null ? [["status code", String(data.status_code)] as [string, string]] : []),
        ...(data.duration_ms !== null ? [["duration", fmtMs(data.duration_ms)] as [string, string]] : []),
        ...(data.metric_name ? [["metric", `${data.metric_name} = ${data.metric_value}`] as [string, string]] : []),
        ["trace id", data.trace_id ?? "—"],
        ["span id", data.span_id ?? "—"],
        ["parent span", data.parent_span_id ?? "—"],
      ]} />
      {data.message && (
        <div>
          <p className="tm-label mb-1.5">message (untrusted input, shown verbatim)</p>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words border border-hairline bg-chip p-2 text-[11px] text-foreground/90">{data.message}</pre>
        </div>
      )}
      <div>
        <p className="tm-label mb-1.5">attributes</p>
        <pre className="max-h-64 overflow-auto border border-hairline bg-chip p-2 text-[11px] text-foreground/90">
          {JSON.stringify(data.attributes, null, 2)}
        </pre>
      </div>
      <KeyValue items={[
        ["ingested", fmtDateTime(data.ingested_at)],
        ["ingestion job", data.ingestion_job_id ?? "—"],
        ["event hash", <span key="h" className="break-all text-[10px] text-dim">{data.event_hash}</span>],
      ]} />
    </div>
  );
}

/** Inline, keyboard-accessible reference to a telemetry event. */
export function EventRef({ id, children, className }: { id: string; children?: React.ReactNode; className?: string }) {
  const open = useEventInspector();
  return (
    <button type="button" onClick={() => open(id)} title={`Open event ${id}`}
      className={cn("tm-chip cursor-pointer border-lavender/30 text-lavender transition-colors hover:border-lavender hover:bg-accent", className)}>
      {children ?? id.slice(0, 8)}
    </button>
  );
}
