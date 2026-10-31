"use client";

import { useRouter } from "next/navigation";
import { createContext, use, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, ApiError, type Meta, type Project, type User } from "@/lib/api";

export type RangeKey = "1h" | "6h" | "24h" | "7d" | "30d" | "all";

interface SessionValue {
  user: User;
  meta: Meta | null;
  projects: Project[];
  project: Project | null;
  environment: string | null;
  range: RangeKey;
  setProjectId: (id: string) => void;
  setEnvironment: (env: string | null) => void;
  setRange: (r: RangeKey) => void;
  refreshProjects: () => Promise<void>;
  logout: () => Promise<void>;
  can: (role: "viewer" | "analyst" | "editor" | "owner") => boolean;
}

const RANK = { viewer: 0, analyst: 1, editor: 2, owner: 3 } as const;

interface SessionState {
  value: SessionValue | null;
  ready: Promise<void>;
}
const SessionContext = createContext<SessionState | null>(null);

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}

function stored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function store(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode): selection simply isn't remembered */
  }
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectIdState] = useState<string | null>(null);
  const [environment, setEnvironmentState] = useState<string | null>(null);
  const [range, setRangeState] = useState<RangeKey>("all");
  const [failed, setFailed] = useState<string | null>(null);
  // Resolves once the session is known. Components that need it suspend on it (see
  // useSession), so pages stay in the tree inside a Suspense boundary instead of being
  // conditionally dropped while the session loads. On the server it never resolves.
  const [gate] = useState(deferred);

  const refreshProjects = useCallback(async () => {
    const list = await api<Project[]>("/projects");
    setProjects(list);
    setProjectIdState((current) => {
      const remembered = current ?? stored("tm.project");
      return list.find((p) => p.id === remembered)?.id ?? list[0]?.id ?? null;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api<User>("/auth/me");
        if (cancelled) return;
        setUser(me);
        setRangeState((stored("tm.range") as RangeKey) ?? "all");
        api<Meta>("/meta").then(setMeta).catch(() => setMeta(null));
        await refreshProjects();
        gate.resolve();
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
        else setFailed(e instanceof ApiError ? e.message : "The API is unreachable.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router, refreshProjects, gate]);

  const project = projects.find((p) => p.id === projectId) ?? null;

  const value = useMemo<SessionValue | null>(() => {
    if (!user) return null;
    return {
      user, meta, projects, project, environment, range,
      setProjectId: (id) => {
        store("tm.project", id);
        setProjectIdState(id);
        setEnvironmentState(null);
      },
      setEnvironment: setEnvironmentState,
      setRange: (r) => {
        store("tm.range", r);
        setRangeState(r);
      },
      refreshProjects,
      logout: async () => {
        await api("/auth/logout", { method: "POST" }).catch(() => undefined);
        store("tm.project", null);
        router.replace("/login");
      },
      can: (role) => (project?.role ? RANK[project.role] >= RANK[role] : false),
    };
  }, [user, meta, projects, project, environment, range, refreshProjects, router]);

  const state = useMemo(() => ({ value, ready: gate.promise }), [value, gate]);

  if (failed) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="tm-panel max-w-md p-6">
          <p className="tm-label mb-2">connection error</p>
          <p className="text-sm">{failed}</p>
          <p className="mt-3 text-xs text-dim">Check that the API is running and reload the page.</p>
        </div>
      </div>
    );
  }
  return <SessionContext.Provider value={state}>{children}</SessionContext.Provider>;
}

function useSessionState(): SessionState {
  const state = useContext(SessionContext);
  if (!state) throw new Error("useSession must be used inside SessionProvider");
  return state;
}

/** The session if it has loaded, otherwise null. Never suspends (for app chrome). */
export function useOptionalSession(): SessionValue | null {
  return useSessionState().value;
}

/** Returns the session, suspending until it has loaded. Use inside a Suspense boundary. */
export function useSession(): SessionValue {
  const state = useSessionState();
  use(state.ready); // always called, so hook order is stable; returns at once when resolved
  if (!state.value) throw new Error("session resolved without a value");
  return state.value;
}
