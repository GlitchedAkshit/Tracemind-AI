import type { Metadata, Viewport } from "next";

import { Toaster } from "@/components/ui/sonner";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "TraceMind AI", template: "%s · TraceMind AI" },
  description: "Evidence-driven incident investigation for distributed systems.",
};

export const viewport: Viewport = { themeColor: "#000000", colorScheme: "dark" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="dark h-full antialiased">
      <body className="min-h-full">
        {children}
        <Toaster position="bottom-right" toastOptions={{ className: "font-mono text-[12px]" }} />
      </body>
    </html>
  );
}
