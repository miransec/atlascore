"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { cn } from "@/lib/cn";

const ROUTES = [
  { href: "/dashboard", label: "Dashboard", group: "Overview" },
  { href: "/dashboard/activity", label: "Activity", group: "Overview" },
  { href: "/dashboard/sources", label: "Sources", group: "Knowledge" },
  { href: "/dashboard/documents", label: "Documents", group: "Knowledge" },
  { href: "/dashboard/search", label: "Search", group: "Knowledge" },
  { href: "/dashboard/answer", label: "Ask AI", group: "Knowledge" },
  { href: "/dashboard/workspaces", label: "Workspaces", group: "Workspace" },
  { href: "/dashboard/members", label: "Members", group: "Workspace" },
  { href: "/dashboard/teams", label: "Teams", group: "Workspace" },
  { href: "/dashboard/api-keys", label: "API Keys", group: "Security" },
  { href: "/dashboard/service-accounts", label: "Service Accounts", group: "Security" },
  { href: "/dashboard/audit", label: "Audit Logs", group: "Security" },
  { href: "/dashboard/settings/organisation", label: "Organisation", group: "Settings" },
  { href: "/dashboard/settings/providers", label: "AI Providers", group: "Settings" },
  { href: "/dashboard/settings/preferences", label: "Preferences", group: "Settings" },
];

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return ROUTES;
    return ROUTES.filter(
      (r) =>
        r.label.toLowerCase().includes(needle) ||
        r.group.toLowerCase().includes(needle),
    );
  }, [q]);

  useEffect(() => {
    if (!open) {
      setQ("");
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => Math.min(i + 1, filtered.length - 1));
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
      }
      if (e.key === "Enter" && filtered[active]) {
        router.push(filtered[active].href);
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, filtered, active, onClose, router]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/55 p-4 pt-[12vh] backdrop-blur-sm animate-fade-in">
      <div
        role="dialog"
        aria-label="Command palette"
        className="w-full max-w-lg overflow-hidden rounded-lg border border-border bg-surface-raised shadow-md animate-slide-up"
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Search className="h-4 w-4 text-muted" />
          <input
            autoFocus
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setActive(0);
            }}
            placeholder="Jump to a page…"
            className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted">
            Esc
          </kbd>
        </div>
        <ul className="max-h-72 overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <li className="px-3 py-4 text-center text-sm text-muted">No matches</li>
          ) : (
            filtered.map((r, i) => (
              <li key={r.href}>
                <button
                  type="button"
                  className={cn(
                    "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm",
                    i === active ? "bg-accent-soft text-accent-hover" : "hover:bg-surface-overlay",
                  )}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => {
                    router.push(r.href);
                    onClose();
                  }}
                >
                  <span>{r.label}</span>
                  <span className="text-[11px] text-muted">{r.group}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
