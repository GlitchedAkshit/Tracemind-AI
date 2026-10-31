"use client";

import { EventInspectorProvider } from "@/components/event-inspector";
import { SessionProvider } from "@/components/session";
import { AppShell } from "@/components/shell";
import { TooltipProvider } from "@/components/ui/tooltip";

/** Client-side providers for every authenticated route. */
export function AppFrame({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <TooltipProvider delayDuration={200}>
        <EventInspectorProvider>
          <AppShell>{children}</AppShell>
        </EventInspectorProvider>
      </TooltipProvider>
    </SessionProvider>
  );
}
