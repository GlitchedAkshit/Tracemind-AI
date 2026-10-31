"use client";

import {
  Activity, BookOpenText, FlaskConical, LayoutDashboard, LogOut, Menu, Network, Settings, ShieldAlert, Upload,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useState } from "react";

import { Loading } from "@/components/common";
import { type RangeKey, useOptionalSession } from "@/components/session";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/ingest", label: "Telemetry", icon: Upload },
  { href: "/services", label: "Dependencies", icon: Network },
  { href: "/incidents", label: "Incidents", icon: ShieldAlert },
  { href: "/evaluation", label: "Evaluation lab", icon: FlaskConical },
  { href: "/reports", label: "Reports", icon: BookOpenText },
  { href: "/settings", label: "Settings", icon: Settings },
];
const RANGES: { key: RangeKey; label: string }[] = [
  { key: "1h", label: "last 1h" }, { key: "6h", label: "last 6h" }, { key: "24h", label: "last 24h" },
  { key: "7d", label: "last 7d" }, { key: "30d", label: "last 30d" }, { key: "all", label: "all data" },
];

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  // usePathname is request data on dynamic routes; the fallback renders the same links unhighlighted
  return (
    <Suspense fallback={<NavLinks pathname={null} onNavigate={onNavigate} />}>
      <CurrentNav onNavigate={onNavigate} />
    </Suspense>
  );
}

function CurrentNav({ onNavigate }: { onNavigate?: () => void }) {
  return <NavLinks pathname={usePathname()} onNavigate={onNavigate} />;
}

function NavLinks({ pathname, onNavigate }: { pathname: string | null; onNavigate?: () => void }) {
  return (
    <nav aria-label="Primary" className="flex flex-col gap-0.5">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname !== null && (href === "/" ? pathname === "/" : pathname.startsWith(href));
        return (
          <Link key={href} href={href} onClick={onNavigate} aria-current={active ? "page" : undefined}
            className={cn(
              "group flex items-center gap-2.5 border-l px-3 py-2 text-[12px] tracking-wide transition-colors",
              active ? "border-lavender bg-accent/60 text-white" : "border-transparent text-dim hover:border-hairline hover:text-foreground",
            )}>
            <Icon className={cn("size-3.5", active ? "text-lavender" : "text-dim group-hover:text-foreground")} aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2 px-3 py-4">
      <span className="relative flex size-5 items-center justify-center">
        <span className="absolute inset-0 rounded-full border border-lavender/60" />
        <span className="size-1.5 rounded-full bg-lavender shadow-[0_0_8px_#aa88ff]" />
      </span>
      <span className="text-[12px] font-medium tracking-[0.3em] text-white">TRACEMIND</span>
    </Link>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  // The chrome renders before the session has loaded; pages suspend on useSession() inside
  // the Suspense boundary around <main>, so every route segment stays in the render tree.
  const session = useOptionalSession();
  const [open, setOpen] = useState(false);
  const project = session?.project ?? null;

  const sidebar = (
    <div className="flex h-full flex-col">
      <Brand />
      <div className="px-3 pb-3">
        <p className="tm-label mb-1.5">project</p>
        <Select value={project?.id ?? ""} onValueChange={(id) => session?.setProjectId(id)} disabled={!session}>
          <SelectTrigger aria-label="Select project" className="h-8 w-full text-[11px]"><SelectValue placeholder="No project" /></SelectTrigger>
          <SelectContent>
            {(session?.projects ?? []).map((p) => (
              <SelectItem key={p.id} value={p.id} className="text-[12px]">
                {p.name}{p.is_demo && !/demo/i.test(p.name) ? " · demo" : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Nav onNavigate={() => setOpen(false)} />
      <div className="mt-auto space-y-3 border-t border-hairline p-3">
        <div className="space-y-1">
          <p className="tm-label">engine</p>
          <p className="text-[11px] text-dim">{session?.meta?.algorithm_version ?? "—"}</p>
          <p className="truncate text-[11px] text-dim" title={session?.meta?.llm_model}>
            writer: {session?.meta?.llm_provider === "deterministic" ? "deterministic" : session?.meta?.llm_model ?? "—"}
          </p>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[11px] text-foreground/80" title={session?.user.email}>
            {session ? (session.user.is_demo ? "demo visitor" : session.user.display_name) : "…"}
          </span>
          <button onClick={() => session?.logout()} className="text-dim transition-colors hover:text-lavender" aria-label="Sign out">
            <LogOut className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:bg-black focus:p-2">Skip to content</a>
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 border-r border-hairline lg:block">{sidebar}</aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b border-hairline bg-black/85 px-3 backdrop-blur sm:gap-3 sm:px-4">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger className="text-dim hover:text-white lg:hidden" aria-label="Open navigation"><Menu className="size-4" /></SheetTrigger>
            <SheetContent side="left" className="w-64 bg-black p-0">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              {sidebar}
            </SheetContent>
          </Sheet>
          <div className="flex min-w-0 items-center gap-2">
            <Activity className="size-3.5 shrink-0 text-lavender" aria-hidden />
            <span className="hidden truncate text-[12px] text-white sm:inline">{project?.name ?? (session ? "No project selected" : "…")}</span>
            {project?.is_demo && (
              <span className="tm-chip shrink-0 whitespace-nowrap border-lavender/40 text-lavender" data-testid="demo-badge">
                <span className="sm:hidden">demo data</span>
                <span className="hidden sm:inline">synthetic demo data</span>
              </span>
            )}
            {project?.role && <span className="tm-chip hidden sm:inline-flex">{project.role}</span>}
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-1.5 sm:gap-2">
            <Select value={session?.environment ?? "__all"} onValueChange={(v) => session?.setEnvironment(v === "__all" ? null : v)} disabled={!session}>
              <SelectTrigger aria-label="Environment" className="h-7 w-[88px] text-[11px] sm:w-[160px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="__all" className="text-[11px]">all environments</SelectItem>
                {project?.environments.map((env) => <SelectItem key={env} value={env} className="text-[11px]">{env}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={session?.range ?? "all"} onValueChange={(v) => session?.setRange(v as RangeKey)} disabled={!session}>
              <SelectTrigger aria-label="Time range" className="h-7 w-[76px] text-[11px] sm:w-[110px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {RANGES.map((r) => <SelectItem key={r.key} value={r.key} className="text-[11px]">{r.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </header>
        <main id="main" className="min-w-0 flex-1 px-4 py-5 sm:px-6">
          <Suspense fallback={<Loading label="initialising session" />}>{children}</Suspense>
        </main>
      </div>
    </div>
  );
}

export function PageHeader({ label, title, children }: { label: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <p className="tm-label">{label}</p>
        <h1 className="mt-1 text-lg font-normal tracking-wide text-white">{title}</h1>
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}
