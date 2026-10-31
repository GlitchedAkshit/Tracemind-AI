"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Empty, ErrorState, Loading, Panel, SeverityBadge, StatusDot } from "@/components/common";
import { PageHeader } from "@/components/shell";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Incident, Page } from "@/lib/api";
import { CAUSE_LABEL, duration, fmtDateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type SortKey = "started_at" | "severity" | "status" | "title";

export default function IncidentsPage() {
  const { project, environment } = useSession();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [status, setStatus] = useState("__any");
  const [severity, setSeverity] = useState("__any");
  const [sort, setSort] = useState<SortKey>("started_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const t = setTimeout(() => { setDebounced(q); setPage(1); }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const qs = new URLSearchParams({
    sort, order, page: String(page), page_size: "15",
    ...(debounced ? { q: debounced } : {}), ...(status !== "__any" ? { status } : {}),
    ...(severity !== "__any" ? { severity } : {}), ...(environment ? { environment } : {}),
  });
  const { data, error, loading, reload } = useApi<Page<Incident>>(project ? `/projects/${project.id}/incidents?${qs}` : null, { pollMs: 15000 });

  if (!project) return <Empty title="Select or create a project first" />;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const header = (key: SortKey | null, label: string) => (
    <th scope="col" className="tm-label px-3 py-2 font-normal" aria-sort={key && sort === key ? (order === "asc" ? "ascending" : "descending") : undefined}>
      {key ? (
        <button className="inline-flex items-center gap-1 uppercase hover:text-white" onClick={() => {
          if (sort === key) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(key); setOrder("desc"); }
        }}>
          {label}{sort === key && (order === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
        </button>
      ) : label}
    </th>
  );

  return (
    <div className="space-y-4">
      <PageHeader label="incident explorer" title="Detected incidents" />
      <Panel bodyClassName="p-0">
        <div className="flex flex-wrap gap-2 border-b border-hairline p-3">
          <Input aria-label="Search incidents" placeholder="search title…" value={q} onChange={(e) => setQ(e.target.value)} className="h-8 w-60 text-[12px]" />
          <Select value={status} onValueChange={(v) => { setStatus(v); setPage(1); }}>
            <SelectTrigger className="h-8 w-36 text-[11px]" aria-label="Filter by status"><SelectValue /></SelectTrigger>
            <SelectContent>{["__any", "open", "investigating", "resolved"].map((s) => <SelectItem key={s} value={s} className="text-[11px]">{s === "__any" ? "any status" : s}</SelectItem>)}</SelectContent>
          </Select>
          <Select value={severity} onValueChange={(v) => { setSeverity(v); setPage(1); }}>
            <SelectTrigger className="h-8 w-36 text-[11px]" aria-label="Filter by severity"><SelectValue /></SelectTrigger>
            <SelectContent>{["__any", "critical", "high", "medium", "low"].map((s) => <SelectItem key={s} value={s} className="text-[11px]">{s === "__any" ? "any severity" : s}</SelectItem>)}</SelectContent>
          </Select>
          <span className="ml-auto self-center text-[11px] text-dim">{data ? `${data.total} incidents` : ""}</span>
        </div>
        {loading && !data ? <div className="px-3"><Loading label="loading incidents" /></div> : error ? <div className="p-3"><ErrorState error={error} retry={reload} /></div> :
          !data?.items.length ? <Empty title="No incidents match">Incidents are created automatically when uploaded telemetry contains correlated anomalies.</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead><tr className="border-b border-hairline text-left">
                  {header("title", "incident")}{header("severity", "severity")}{header("status", "status")}{header("started_at", "started")}
                  {header(null, "affected services")}{header(null, "leading hypothesis")}{header(null, "analysis")}
                </tr></thead>
                <tbody>
                  {data.items.map((i) => (
                    <tr key={i.id} className="border-b border-hairline/60 align-top last:border-0 hover:bg-white/[0.02]">
                      <td className="min-w-[220px] max-w-[340px] px-3 py-2">
                        <Link href={`/incidents/${i.id}`} className="text-white hover:text-lavender">{i.title}</Link>
                        <p className="mt-0.5 text-[10px] text-dim">{i.environment} · {i.anomaly_count} anomalies · {duration(i.started_at, i.ended_at)}</p>
                      </td>
                      <td className="px-3 py-2"><SeverityBadge severity={i.severity} /></td>
                      <td className="px-3 py-2"><StatusDot status={i.status} /></td>
                      <td className="whitespace-nowrap px-3 py-2 tabular-nums text-foreground/80">{fmtDateTime(i.started_at)}</td>
                      <td className="max-w-[240px] px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {i.affected_services.slice(0, 4).map((s) => <span key={s} className="tm-chip">{s}</span>)}
                          {i.affected_services.length > 4 && <span className="tm-chip text-dim">+{i.affected_services.length - 4}</span>}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        {i.leading_hypothesis ? (
                          <><span className="text-lavender">{i.leading_hypothesis}</span>
                            <p className="text-[10px] text-dim">{CAUSE_LABEL[i.leading_cause_type ?? ""] ?? i.leading_cause_type}</p></>
                        ) : <span className="text-dim">—</span>}
                      </td>
                      <td className="px-3 py-2">{i.analysis_status ? <StatusDot status={i.analysis_status} /> : <span className="text-[11px] text-dim">not analysed</span>}</td>
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
    </div>
  );
}
