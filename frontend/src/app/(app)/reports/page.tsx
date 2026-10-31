"use client";

import { Download } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Empty, ErrorState, Loading, Panel, SeverityBadge } from "@/components/common";
import { PageHeader } from "@/components/shell";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { apiText, downloadText, type Report, type ReportListItem } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export default function ReportsPage() {
  const { project } = useSession();
  const list = useApi<ReportListItem[]>(project ? `/projects/${project.id}/reports` : null);
  const [selected, setSelected] = useState<string | null>(null);
  const reportId = selected ?? list.data?.[0]?.id ?? null;
  const report = useApi<Report>(reportId ? `/reports/${reportId}` : null);

  if (!project) return <Empty title="Select or create a project first" />;

  const download = async () => {
    if (!reportId) return;
    const { text, filename } = await apiText(`/reports/${reportId}/markdown`);
    downloadText(text, filename, "text/markdown");
  };

  return (
    <div className="space-y-4">
      <PageHeader label="reports & postmortems" title="Generated investigation reports" />
      <div className="grid gap-3 xl:grid-cols-[320px_1fr]">
        <Panel label="reports" bodyClassName="p-0">
          {list.loading && !list.data ? <div className="px-3"><Loading /></div> : list.error ? <div className="p-3"><ErrorState error={list.error} /></div> :
            !list.data?.length ? <Empty title="No reports yet">Open an analysed incident and choose “export report”.</Empty> : (
              <ul>
                {list.data.map((r) => (
                  <li key={r.id}>
                    <button onClick={() => setSelected(r.id)} className={cn("w-full border-b border-l-2 border-b-hairline/60 px-3 py-2 text-left",
                      r.id === reportId ? "border-l-lavender bg-accent/50" : "border-l-transparent hover:bg-white/[0.02]")}>
                      <p className="truncate text-[12px] text-white">{r.incident_title}</p>
                      <div className="mt-1 flex items-center gap-2 text-[10px] text-dim"><SeverityBadge severity={r.severity} />{fmtDateTime(r.created_at)}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
        </Panel>
        <Panel label="markdown preview" title={report.data ? `${report.data.algorithm_version} · ${report.data.model_version}` : "—"}
          actions={report.data && <div className="flex gap-2">
            <Link href={`/incidents/${report.data.incident_id}`} className="tm-link self-center text-[11px]">incident</Link>
            <Button size="sm" variant="outline" className="h-7 text-[11px]" onClick={download}><Download className="size-3" /> .md</Button>
          </div>}>
          {report.loading && !report.data ? <Loading /> : report.error ? <ErrorState error={report.error} /> : !report.data ? <Empty title="Select a report" /> : (
            <pre className="max-h-[75vh] overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-foreground/90">{report.data.content_markdown}</pre>
          )}
        </Panel>
      </div>
    </div>
  );
}
