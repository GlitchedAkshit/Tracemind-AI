"use client";

import Link from "next/link";
import { useState } from "react";

import { Empty, ErrorState, KeyValue, Loading, Panel, StatusDot } from "@/components/common";
import { EventRef } from "@/components/event-inspector";
import { DependencyGraph } from "@/components/service-graph";
import { PageHeader } from "@/components/shell";
import { useSession } from "@/components/session";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { GraphEdge, Incident, Page, ServiceGraph } from "@/lib/api";
import { fmtMs, fmtNum, fmtPct, fmtTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

export default function ServicesPage() {
  const { project, environment } = useSession();
  const [incidentId, setIncidentId] = useState<string>("__none");
  const [selected, setSelected] = useState<string | null>(null);
  const [edge, setEdge] = useState<GraphEdge | null>(null);
  const incidents = useApi<Page<Incident>>(project ? `/projects/${project.id}/incidents?page_size=50` : null);
  const qs = new URLSearchParams({
    ...(environment ? { environment } : {}),
    ...(incidentId !== "__none" ? { incident_id: incidentId } : {}),
  });
  const graph = useApi<ServiceGraph>(project ? `/projects/${project.id}/service-graph?${qs}` : null);

  if (!project) return <Empty title="Select or create a project first" />;
  const node = graph.data?.nodes.find((n) => n.id === selected);

  return (
    <div className="space-y-4">
      <PageHeader label="service dependency explorer" title="Topology derived from spans and declared dependencies">
        <Select value={incidentId} onValueChange={(v) => { setIncidentId(v); setSelected(null); setEdge(null); }}>
          <SelectTrigger className="h-8 w-[280px] text-[11px]" aria-label="Highlight incident"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="__none" className="text-[11px]">all traffic (no incident highlight)</SelectItem>
            {incidents.data?.items.map((i) => (
              <SelectItem key={i.id} value={i.id} className="text-[11px]">{fmtTime(i.started_at).slice(0, 5)} · {i.title}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </PageHeader>

      <div className="grid gap-3 xl:grid-cols-[1fr_320px]">
        <Panel label="graph" title={graph.data ? `${graph.data.nodes.length} services · ${graph.data.edges.length} edges · ${fmtNum(graph.data.events_considered)} events considered` : "Loading"}
          bodyClassName="p-0">
          {graph.loading && !graph.data ? <div className="p-3"><Loading label="building graph" /></div> :
            graph.error ? <div className="p-3"><ErrorState error={graph.error} retry={graph.reload} /></div> :
              !graph.data?.nodes.length ? <Empty title="No services yet">Upload traces with parent/child spans or dependency events.</Empty> : (
                <DependencyGraph nodes={graph.data.nodes} edges={graph.data.edges} selected={selected} height={560}
                  onSelect={(id) => { setSelected(id); setEdge(null); }} onSelectEdge={(e) => { setEdge(e); setSelected(null); }} />
              )}
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-hairline px-3 py-2 text-[10px] text-dim">
            <span>edges point caller → callee</span>
            <span><span className="text-sev-critical">red</span>: ≥5 % failing calls</span>
            <span><span className="text-lavender">lavender border</span>: leading hypothesis</span>
            <span>dashed: declared dependency without observed spans</span>
          </div>
        </Panel>

        <div className="space-y-3">
          {graph.data?.leading_service && (
            <Panel label="incident focus">
              <p className="text-[12px]">Leading hypothesis: <span className="text-lavender">{graph.data.leading_service}</span></p>
              <p className="mt-1 text-[11px] text-dim">
                Blast radius (services that transitively call it): {graph.data.nodes.filter((n) => n.in_blast_radius).map((n) => n.id).join(", ") || "none"}
              </p>
              <Link href={`/incidents/${incidentId}`} className="tm-link mt-2 inline-block text-[11px]">open investigation →</Link>
            </Panel>
          )}
          {node ? (
            <Panel label="service" title={node.id}>
              <KeyValue items={[
                ["status", <StatusDot key="s" status={node.status} />],
                ["kind", node.kind],
                ["requests", fmtNum(node.requests)],
                ["error rate", fmtPct(node.error_rate, 2)],
                ["p95 latency", fmtMs(node.latency_p95_ms)],
                ["open incidents", String(node.open_incidents)],
                ["affected", node.affected ? "yes (in selected incident)" : "no"],
              ]} />
              <p className="tm-label mb-1 mt-3">upstream · callers</p>
              <ul className="flex flex-wrap gap-1">{node.callers.length ? node.callers.map((c) => <li key={c}><button className="tm-chip hover:border-lavender" onClick={() => setSelected(c)}>{c}</button></li>) : <li className="text-[11px] text-dim">none (entry point)</li>}</ul>
              <p className="tm-label mb-1 mt-3">downstream · callees</p>
              <ul className="flex flex-wrap gap-1">{node.callees.length ? node.callees.map((c) => <li key={c}><button className="tm-chip hover:border-lavender" onClick={() => setSelected(c)}>{c}</button></li>) : <li className="text-[11px] text-dim">none (leaf)</li>}</ul>
            </Panel>
          ) : edge ? (
            <Panel label="edge evidence" title={`${edge.source} → ${edge.target}`}>
              <KeyValue items={[
                ["derived from", edge.origins.join(", ")],
                ["calls", fmtNum(edge.call_count)],
                ["failed calls", `${fmtNum(edge.error_count)} (${fmtPct(edge.error_rate)})`],
                ["p95 latency", fmtMs(edge.latency_p95_ms)],
              ]} />
              <p className="tm-label mb-1 mt-3">events behind this edge</p>
              <div className="flex flex-wrap gap-1">{edge.evidence_event_ids.map((id) => <EventRef key={id} id={id} />)}</div>
            </Panel>
          ) : (
            <Panel label="details"><Empty title="Select a service or an edge">Edges keep the span or dependency events they were derived from.</Empty></Panel>
          )}
        </div>
      </div>
    </div>
  );
}
