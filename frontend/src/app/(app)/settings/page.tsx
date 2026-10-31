"use client";

import { Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Empty, ErrorState, KeyValue, Loading, Panel } from "@/components/common";
import { PageHeader } from "@/components/shell";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, type Member, type Project } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

const SETTINGS: { key: string; label: string; def: number; step: number; help: string }[] = [
  { key: "z_threshold", label: "robust z threshold", def: 4, step: 0.5, help: "Robust deviation (MAD-scaled) required before a point can be anomalous." },
  { key: "min_error_rate", label: "min error rate", def: 0.05, step: 0.01, help: "Absolute error-rate floor for error spikes." },
  { key: "min_error_count", label: "min errors / bucket", def: 3, step: 1, help: "Errors needed in a bucket before its rate counts as evidence." },
  { key: "min_latency_delta_ms", label: "min latency delta (ms)", def: 80, step: 10, help: "Absolute p95 increase required for latency spikes." },
  { key: "baseline_buckets", label: "baseline window (buckets)", def: 30, step: 1, help: "Rolling baseline length." },
  { key: "bucket_seconds", label: "bucket size (s)", def: 60, step: 10, help: "Time-series resolution." },
  { key: "correlation_gap_minutes", label: "correlation gap (min)", def: 5, step: 1, help: "Max gap between related anomalies in one incident." },
  { key: "deployment_lookback_minutes", label: "deployment lookback (min)", def: 30, step: 5, help: "How far before onset deployments are considered." },
];

export default function SettingsPage() {
  const { project, can, refreshProjects, setProjectId, user } = useSession();
  return (
    <div className="space-y-4">
      <PageHeader label="settings" title="Projects, environments, analysis and access" />
      <div className="grid gap-3 xl:grid-cols-2">
        <CreateProject onCreated={async (p) => { await refreshProjects(); setProjectId(p.id); }} demo={user.is_demo} />
        {project ? <ProjectSettings key={project.id} project={project} editable={can("owner")} onSaved={refreshProjects} /> : <Panel><Empty title="No project selected" /></Panel>}
      </div>
      {project && <Members project={project} editable={can("owner") && !project.is_demo} />}
    </div>
  );
}

function CreateProject({ onCreated, demo }: { onCreated: (p: Project) => void; demo: boolean }) {
  const [name, setName] = useState("");
  const [envs, setEnvs] = useState("production, staging");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const p = await api<Project>("/projects", { method: "POST", json: { name, environments: envs.split(",").map((s) => s.trim()).filter(Boolean) } });
      toast.success(`Project “${p.name}” created`);
      setName("");
      onCreated(p);
    } catch (err) {
      toast.error((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel label="new project" title={demo ? "Sandbox project (demo sessions may create up to 3)" : "Create a project"}>
      <form onSubmit={submit} className="space-y-3">
        <label className="block space-y-1"><span className="tm-label">name</span>
          <Input required minLength={2} maxLength={120} value={name} onChange={(e) => setName(e.target.value)} className="h-8 text-[12px]" /></label>
        <label className="block space-y-1"><span className="tm-label">environments (comma separated)</span>
          <Input value={envs} onChange={(e) => setEnvs(e.target.value)} className="h-8 text-[12px]" /></label>
        <Button size="sm" type="submit" disabled={busy || name.trim().length < 2}>create project</Button>
      </form>
    </Panel>
  );
}

function ProjectSettings({ project, editable, onSaved }: { project: Project; editable: boolean; onSaved: () => Promise<void> }) {
  const [description, setDescription] = useState(project.description);
  const [envs, setEnvs] = useState(project.environments.join(", "));
  const [values, setValues] = useState<Record<string, number>>(() =>
    Object.fromEntries(SETTINGS.map((s) => [s.key, project.analysis_settings[s.key] ?? s.def])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/projects/${project.id}`, { method: "PATCH", json: {
        description, environments: envs.split(",").map((s) => s.trim()).filter(Boolean), analysis_settings: values,
      } });
      await onSaved();
      toast.success("Settings saved; new analyses use them");
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel label="project" title={project.name}>
      <KeyValue items={[["id", project.id], ["slug", project.slug], ["your role", project.role ?? "—"], ["created", fmtDateTime(project.created_at)]]} />
      <div className="mt-4 space-y-3">
        <label className="block space-y-1"><span className="tm-label">description</span>
          <Textarea disabled={!editable} value={description} onChange={(e) => setDescription(e.target.value)} className="min-h-[60px] font-sans text-[12px]" /></label>
        <label className="block space-y-1"><span className="tm-label">environments</span>
          <Input disabled={!editable} value={envs} onChange={(e) => setEnvs(e.target.value)} className="h-8 text-[12px]" /></label>
        <div>
          <p className="tm-label mb-2">analysis settings</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {SETTINGS.map((s) => (
              <label key={s.key} className="block space-y-0.5" title={s.help}>
                <span className="text-[11px] text-foreground/80">{s.label}</span>
                <Input type="number" step={s.step} disabled={!editable} value={values[s.key]}
                  onChange={(e) => setValues({ ...values, [s.key]: Number(e.target.value) })} className="h-7 text-[12px]" />
                <span className="block font-sans text-[10px] text-dim">{s.help}</span>
              </label>
            ))}
          </div>
        </div>
        {error && <ErrorState error={error} />}
        {editable ? <Button size="sm" onClick={save} disabled={busy}>save settings</Button> : <p className="text-[11px] text-dim">Only project owners can change settings.</p>}
      </div>
    </Panel>
  );
}

function Members({ project, editable }: { project: Project; editable: boolean }) {
  const members = useApi<Member[]>(`/projects/${project.id}/members`);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api(`/projects/${project.id}/members`, { method: "POST", json: { email, role } });
      setEmail("");
      members.reload();
    } catch (err) {
      toast.error((err as ApiError).message);
    }
  };
  const remove = async (id: string) => {
    try {
      await api(`/projects/${project.id}/members/${id}`, { method: "DELETE" });
      members.reload();
    } catch (err) {
      toast.error((err as ApiError).message);
    }
  };
  return (
    <Panel label="access control" title="Project members and roles" bodyClassName="p-0">
      <p className="border-b border-hairline px-3 py-2 font-sans text-[11px] text-dim">
        owner: everything · editor: upload telemetry · analyst: run analyses, ask the assistant, export reports · viewer: read only.
        Non-members cannot discover the project.
      </p>
      {members.loading && !members.data ? <div className="px-3"><Loading /></div> : members.error ? <div className="p-3"><ErrorState error={members.error} /></div> : (
        <table className="w-full text-[12px]">
          <thead><tr className="border-b border-hairline text-left">{["member", "email", "role", "since", ""].map((h) => <th key={h} scope="col" className="tm-label px-3 py-2 font-normal">{h}</th>)}</tr></thead>
          <tbody>
            {members.data?.map((m) => (
              <tr key={m.id} className="border-b border-hairline/60 last:border-0">
                <td className="px-3 py-1.5 text-white">{m.display_name}</td><td className="px-3 py-1.5 text-dim">{m.email}</td>
                <td className="px-3 py-1.5">{m.role}</td><td className="px-3 py-1.5 text-dim">{fmtDateTime(m.created_at)}</td>
                <td className="px-3 py-1.5 text-right">{editable && <button aria-label={`Remove ${m.display_name}`} onClick={() => remove(m.id)} className="text-dim hover:text-sev-critical"><Trash2 className="size-3.5" /></button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {editable && (
        <form onSubmit={add} className="flex flex-wrap gap-2 border-t border-hairline p-3">
          <Input type="email" required placeholder="registered user email" value={email} onChange={(e) => setEmail(e.target.value)} className="h-8 w-64 text-[12px]" aria-label="Member email" />
          <Select value={role} onValueChange={setRole}>
            <SelectTrigger className="h-8 w-32 text-[12px]" aria-label="Role"><SelectValue /></SelectTrigger>
            <SelectContent>{["viewer", "analyst", "editor", "owner"].map((r) => <SelectItem key={r} value={r} className="text-[12px]">{r}</SelectItem>)}</SelectContent>
          </Select>
          <Button size="sm" type="submit">add member</Button>
        </form>
      )}
    </Panel>
  );
}
