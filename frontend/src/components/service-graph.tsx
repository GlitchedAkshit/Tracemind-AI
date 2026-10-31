"use client";

import "@xyflow/react/dist/style.css";

import { Background, Controls, type Edge, Handle, MarkerType, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";
import { memo, useMemo } from "react";

import type { GraphEdge, GraphNode } from "@/lib/api";
import { fmtMs, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";

type ServiceNodeData = GraphNode & { selected: boolean } & Record<string, unknown>;

const STATUS_RING: Record<GraphNode["status"], string> = {
  healthy: "border-ok/40", degraded: "border-sev-medium/60", critical: "border-sev-critical/70", no_data: "border-hairline",
};

const ServiceNode = memo(function ServiceNode({ data }: NodeProps<Node<ServiceNodeData>>) {
  return (
    <div className={cn(
      "w-[176px] border bg-black/90 px-2.5 py-2 font-mono transition-shadow",
      STATUS_RING[data.status],
      data.affected && "shadow-[0_0_0_1px_rgba(255,107,129,0.35)]",
      data.leading_hypothesis && "border-lavender shadow-[0_0_18px_rgba(170,136,255,0.35)]",
      data.selected && "outline outline-1 outline-offset-2 outline-lavender",
    )}>
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-dim" />
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-[11px] text-white">{data.id}</span>
        {data.leading_hypothesis && <span className="text-[9px] uppercase tracking-wider text-lavender">lead</span>}
        {!data.leading_hypothesis && data.affected && <span className="text-[9px] uppercase tracking-wider text-sev-critical">affected</span>}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-dim">
        <span>err {fmtPct(data.error_rate)}</span>
        <span>p95 {fmtMs(data.latency_p95_ms)}</span>
      </div>
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-dim" />
    </div>
  );
});

const nodeTypes = { service: ServiceNode };

export function DependencyGraph({ nodes, edges, selected, onSelect, onSelectEdge, height = 460 }: {
  nodes: GraphNode[]; edges: GraphEdge[]; selected?: string | null; onSelect?: (id: string | null) => void;
  onSelectEdge?: (edge: GraphEdge) => void; height?: number;
}) {
  const flow = useMemo(() => {
    // layered layout: callers on the left, callees to the right
    const columns = new Map<number, GraphNode[]>();
    for (const n of nodes) columns.set(n.depth, [...(columns.get(n.depth) ?? []), n]);
    const rfNodes: Node<ServiceNodeData>[] = [];
    for (const [depth, list] of columns) {
      list.sort((a, b) => a.id.localeCompare(b.id));
      list.forEach((n, i) => {
        rfNodes.push({
          id: n.id, type: "service", position: { x: depth * 230, y: (i - (list.length - 1) / 2) * 78 },
          data: { ...n, selected: n.id === selected }, draggable: true,
        });
      });
    }
    const rfEdges: Edge[] = edges.map((e) => {
      const failing = (e.error_rate ?? 0) >= 0.05;
      const hot = nodes.find((n) => n.id === e.target)?.affected && nodes.find((n) => n.id === e.source)?.affected;
      const color = failing ? "#ff6b81" : hot ? "#aa88ff" : "#3a3a3a";
      return {
        id: e.id, source: e.source, target: e.target, animated: failing,
        style: { stroke: color, strokeWidth: failing ? 1.6 : 1, strokeDasharray: e.origins.includes("explicit") && !e.origins.includes("span") ? "4 4" : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 12, height: 12 },
        label: e.call_count ? `${e.call_count} calls${e.error_count ? ` · ${e.error_count} err` : ""}` : "declared",
        labelStyle: { fill: "#8a8a8a", fontSize: 9, fontFamily: "var(--font-mono-stack)" },
        labelBgStyle: { fill: "#000", fillOpacity: 0.85 },
        data: e as unknown as Record<string, unknown>,
      };
    });
    return { rfNodes, rfEdges };
  }, [nodes, edges, selected]);

  return (
    <div style={{ height }} className="tm-graph w-full">
      <ReactFlow
        nodes={flow.rfNodes} edges={flow.rfEdges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.15 }}
        minZoom={0.3} maxZoom={1.8} proOptions={{ hideAttribution: true }} colorMode="dark"
        onNodeClick={(_, n) => onSelect?.(n.id)} onPaneClick={() => onSelect?.(null)}
        onEdgeClick={(_, e) => onSelectEdge?.(e.data as unknown as GraphEdge)}
        nodesConnectable={false} elementsSelectable
      >
        <Background color="#1a1a1a" gap={22} size={1} />
        <Controls showInteractive={false} className="!border-hairline" />
      </ReactFlow>
    </div>
  );
}
