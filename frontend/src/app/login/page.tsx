"use client";

import { ArrowRight, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { ParticleField } from "@/components/particles/ParticleField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const card = useRef<HTMLDivElement>(null);

  const run = async (kind: string, fn: () => Promise<unknown>) => {
    setBusy(kind);
    setError(null);
    try {
      await fn();
      router.replace("/");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not reach the API.");
      setBusy(null);
    }
  };

  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const body = { email: String(form.get("email")), password: String(form.get("password")), display_name: String(form.get("name") ?? "") };
    run(mode, () => api(`/auth/${mode}`, { method: "POST", json: mode === "login" ? { email: body.email, password: body.password } : body }));
  };

  return (
    <main className="relative flex min-h-dvh flex-col overflow-x-hidden">
      <header className="relative z-10 flex items-start justify-between gap-4 px-4 pt-5 sm:px-10 sm:pt-8">
        <div>
          <p className="text-[12px] tracking-[0.35em] text-white">TRACEMIND</p>
          <p className="tm-label mt-2 hidden sm:block">evidence-driven incident investigation</p>
        </div>
        <p className="tm-label hidden text-right md:block">logs · traces · metrics · deployments</p>
      </header>

      {/* Phones: the ring gets its own band above the card. sm and up: a fixed full-screen ring
          that scales itself so the card sits inside its hollow centre (frameRef). */}
      <ParticleField className="-mb-6 h-[30vh] min-h-[180px] w-full sm:fixed sm:inset-0 sm:mb-0 sm:h-auto" zoom={1.3}
        density={1.6} tube={0.7} frameRef={card} label="TraceMind particle ring" />
      <div aria-hidden className="pointer-events-none fixed inset-0 hidden bg-[radial-gradient(ellipse_at_center,rgba(0,0,0,0)_0%,rgba(0,0,0,0)_55%,rgba(0,0,0,0.55)_100%)] sm:block" />

      <section className="relative z-10 flex flex-1 items-start justify-center px-4 pb-8 sm:items-center sm:py-12">
        <div ref={card} className="w-full max-w-[400px] border border-hairline bg-black/70 p-6 shadow-[0_0_80px_-20px_rgba(170,136,255,0.35)] backdrop-blur-md sm:bg-black/60 sm:px-8 sm:py-7">
          <p className="tm-label">access</p>
          <h1 className="mt-1 text-lg font-normal text-white">{mode === "login" ? "Sign in" : "Create an account"}</h1>
          <p className="mt-2 font-sans text-[13px] leading-relaxed text-dim [@media(max-height:820px)]:hidden">
            Correlate logs, traces, metrics and deployments; rank root-cause hypotheses with the evidence for and against each.
          </p>

          <Button className="mt-5 h-10 w-full justify-between text-[12px]" disabled={busy !== null}
            onClick={() => run("demo", () => api("/auth/demo", { method: "POST" }))}>
            <span className="truncate">Explore the synthetic demo project</span>
            {busy === "demo" ? <Loader2 className="size-4 shrink-0 animate-spin" /> : <ArrowRight className="size-4 shrink-0" />}
          </Button>
          <p className="mt-1.5 text-[10px] text-dim">Generated telemetry with injected faults. No real data, read-mostly access.</p>

          <div className="my-5 flex items-center gap-3"><span className="h-px flex-1 bg-hairline" /><span className="tm-label">or</span><span className="h-px flex-1 bg-hairline" /></div>

          <form onSubmit={submit} className="space-y-3">
            {mode === "register" && (
              <div className="space-y-1">
                <Label htmlFor="name" className="tm-label">display name</Label>
                <Input id="name" name="name" required maxLength={120} autoComplete="name" className="bg-black/50" />
              </div>
            )}
            <div className="space-y-1">
              <Label htmlFor="email" className="tm-label">email</Label>
              <Input id="email" name="email" type="email" required autoComplete="email" className="bg-black/50" />
            </div>
            <div className="space-y-1">
              <Label htmlFor="password" className="tm-label">password {mode === "register" && "(min 10 characters)"}</Label>
              <Input id="password" name="password" type="password" required minLength={mode === "register" ? 10 : 1}
                autoComplete={mode === "login" ? "current-password" : "new-password"} className="bg-black/50" />
            </div>
            {error && <p role="alert" className="text-[12px] text-sev-critical">{error}</p>}
            <Button type="submit" variant="outline" className="h-9 w-full text-[12px]" disabled={busy !== null}>
              {busy === mode ? <Loader2 className="size-4 animate-spin" /> : mode === "login" ? "Sign in" : "Create account"}
            </Button>
          </form>
          <button className="tm-link mt-4 text-left text-[11px]" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}>
            {mode === "login" ? "No account? Register" : "Have an account? Sign in"}
          </button>
        </div>
      </section>

      <footer className="relative z-10 px-4 pb-5 text-center">
        <p className="tm-label">observed evidence · ranked hypotheses · validated citations</p>
      </footer>
    </main>
  );
}
