"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { RefreshCcw } from "lucide-react";

import { useApiToken } from "@/components/providers/app-providers";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { navItems } from "@/lib/nav";
import { cn } from "@/lib/utils";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { token, setToken } = useApiToken();
  const [draftToken, setDraftToken] = React.useState(token);

  React.useEffect(() => {
    setDraftToken(token);
  }, [token]);

  return (
    <div className="app-shell-root min-h-screen">
      <div className="grid min-h-screen lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="app-shell-sidebar lg:sticky lg:top-0 lg:h-screen">
          <div className="border-b border-[var(--border-subtle)] p-6">
            <div className="flex items-center gap-3">
              <div className="app-shell-brand flex h-11 w-11 items-center justify-center rounded-2xl text-sm font-semibold">
                IMS
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-[var(--text-subtle)]">Operations Console</p>
                <h2 className="text-xl font-semibold text-[var(--text-strong)]">Incident Workflow</h2>
              </div>
            </div>
          </div>

          <nav className="space-y-2 p-4">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "flex items-start gap-3 rounded-2xl px-4 py-3 transition-colors",
                    active
                      ? "bg-[var(--accent-soft)] text-[var(--accent)] ring-1 ring-[var(--accent-ring)]"
                      : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]",
                  )}
                >
                  <Icon className="mt-0.5 h-4 w-4" />
                  <div className="min-w-0">
                    <div className="font-medium">{item.label}</div>
                    <div className="text-xs text-[var(--text-subtle)]">{item.description}</div>
                  </div>
                </Link>
              );
            })}
          </nav>

          <div className="border-t border-[var(--border-subtle)] p-4">
            <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
              <p className="text-xs uppercase tracking-[0.25em] text-[var(--text-subtle)]">API Token</p>
              <Input
                className="mt-3"
                value={draftToken}
                onChange={(event) => setDraftToken(event.target.value)}
                placeholder="demo-token"
              />
              <Button variant="secondary" className="mt-3 w-full" onClick={() => setToken(draftToken)}>
                Apply token
              </Button>
              <p className="mt-3 text-xs text-[var(--text-subtle)]">
                Kept locally for the same-origin control-plane proxy used by the UI.
              </p>
            </div>
          </div>
        </aside>

        <main className="app-shell-main min-w-0">
          <div className="app-shell-topbar sticky top-0 z-10 px-4 py-4 md:px-8">
            <div className="flex items-center justify-end gap-2">
              <ThemeToggle />
              <Button variant="secondary" onClick={() => window.location.reload()}>
                <RefreshCcw className="mr-2 h-4 w-4" />
                Refresh
              </Button>
            </div>
          </div>
          <div className="px-4 py-8 md:px-8">{children}</div>
        </main>
      </div>
    </div>
  );
}
