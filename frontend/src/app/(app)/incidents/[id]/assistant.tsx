"use client";

import { Loader2, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Empty, ErrorState, Panel } from "@/components/common";
import { EventRef } from "@/components/event-inspector";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, type Investigation } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "What changed immediately before the first failure?",
  "Which service is the most plausible starting point for investigation?",
  "What evidence supports the leading hypothesis?",
  "What evidence contradicts it?",
  "What should I investigate next?",
];

export function Assistant({ incidentId, canAsk, hasAnalysis, className }: { incidentId: string; canAsk: boolean; hasAnalysis: boolean; className?: string }) {
  const history = useApi<Investigation[]>(`/incidents/${incidentId}/investigations`);
  const [asked, setAsked] = useState<Investigation[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const list = useRef<HTMLDivElement>(null);

  const known = new Set((history.data ?? []).map((m) => m.id));
  const items = [...(history.data ?? []), ...asked.filter((m) => !known.has(m.id))];
  // keep the newest answer visible by scrolling the message list itself, never the page
  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items.length, busy]);

  const ask = async (q: string) => {
    if (!q.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const answer = await api<Investigation>(`/incidents/${incidentId}/investigate`, { method: "POST", json: { question: q.trim() } });
      setAsked((prev) => [...prev, answer]);
      setQuestion("");
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel label="ai investigation assistant" title="Answers cite only this incident's evidence" className={className} bodyClassName="flex flex-col gap-3">
      <div ref={list} className="max-h-[420px] min-h-[120px] space-y-4 overflow-y-auto pr-1" aria-live="polite">
        {items.length === 0 && (
          <Empty title="Ask about this incident">{hasAnalysis ? "Answers are grounded in the ranked hypotheses and linked telemetry." : "Run an analysis first for the most useful answers."}</Empty>
        )}
        {items.map((m) => (
          <div key={m.id} className="space-y-1.5">
            <p className="text-[12px] text-white"><span className="text-dim">Q ›</span> {m.question}</p>
            <div className={cn("border-l-2 pl-3", m.insufficient_evidence ? "border-sev-medium" : "border-lavender/60")}>
              {m.insufficient_evidence && <p className="tm-label mb-1 text-sev-medium">insufficient evidence</p>}
              <p className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-foreground/90">{m.answer}</p>
              {m.evidence.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {m.evidence.map((e) => <EventRef key={e.event_id} id={e.event_id}>{fmtTime(e.occurred_at)} · {e.service}</EventRef>)}
                </div>
              )}
              {m.suggested_checks.length > 0 && (
                <ul className="mt-2 space-y-0.5 font-sans text-[12px] text-foreground/80">
                  {m.suggested_checks.map((c, i) => <li key={i}>› {c}</li>)}
                </ul>
              )}
              {m.uncertainties.length > 0 && <p className="mt-2 text-[10px] text-dim">{m.uncertainties.join(" ")}</p>}
              <p className="mt-1 text-[10px] text-dim">
                {m.writer === "llm" ? m.model_version : "deterministic writer"}
                {m.fallback_reason && ` · LLM fallback: ${m.fallback_reason}`}
                {m.citations_rejected > 0 && ` · ${m.citations_rejected} fabricated citation(s) removed`}
              </p>
            </div>
          </div>
        ))}
        {busy && <p className="flex items-center gap-2 text-[11px] text-dim"><Loader2 className="size-3 animate-spin text-lavender" /> retrieving evidence…</p>}
      </div>
      {error && <ErrorState error={error} />}
      <div className="flex flex-wrap gap-1">
        {SUGGESTIONS.map((s) => (
          <button key={s} disabled={!canAsk || busy} onClick={() => ask(s)} className="tm-chip text-left text-dim hover:border-lavender hover:text-white disabled:opacity-40">{s}</button>
        ))}
      </div>
      <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
        <Textarea aria-label="Ask a question about this incident" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000}
          placeholder={canAsk ? "Ask about this incident…" : "Your role cannot query the assistant"} disabled={!canAsk}
          className="min-h-[38px] resize-none text-[12px]" rows={1}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(question); } }} />
        <Button type="submit" size="sm" disabled={!canAsk || busy || question.trim().length < 3} aria-label="Send question"><Send className="size-3.5" /></Button>
      </form>
    </Panel>
  );
}
