"use client";

import { Bell, Search } from "lucide-react";
import { StatusBadge } from "@/components/ui/status-badge";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import type { Workspace } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export function Topbar({
  workspaces,
  workspacesLoading,
  onSwitchWorkspace,
  onCreateWorkspace,
  onOpenCommand,
  providerLabel,
  providerHealthy,
}: {
  workspaces: Workspace[];
  workspacesLoading?: boolean;
  onSwitchWorkspace: (id: string) => void;
  onCreateWorkspace: () => void;
  onOpenCommand: () => void;
  providerLabel?: string;
  providerHealthy?: boolean;
}) {
  const { user } = useAuth();
  const initials =
    user?.full_name
      ?.split(" ")
      .map((p) => p[0])
      .join("")
      .slice(0, 2)
      .toUpperCase() ?? "?";

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur-md sm:px-6">
      <WorkspaceSwitcher
        workspaces={workspaces}
        currentId={user?.workspace_id ?? null}
        loading={workspacesLoading}
        onSwitch={onSwitchWorkspace}
        onCreate={onCreateWorkspace}
      />

      <button
        type="button"
        onClick={onOpenCommand}
        className="ml-auto hidden h-9 min-w-[220px] items-center gap-2 rounded-md border border-border bg-surface px-3 text-sm text-muted hover:bg-surface-raised md:inline-flex"
      >
        <Search className="h-3.5 w-3.5" />
        <span className="flex-1 text-left">Search pages…</span>
        <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px]">⌘K</kbd>
      </button>

      <StatusBadge tone={providerHealthy === false ? "warning" : "success"} pulse>
        {providerLabel ?? "provider"}
      </StatusBadge>

      <button
        type="button"
        className="rounded-md p-2 text-muted hover:bg-surface-raised hover:text-foreground"
        aria-label="Activity"
        onClick={onOpenCommand}
      >
        <Bell className="h-4 w-4" />
      </button>

      <div
        className="flex h-8 w-8 items-center justify-center rounded-full border border-border bg-accent-soft text-xs font-semibold text-accent-hover"
        title={user?.email}
      >
        {initials}
      </div>
    </header>
  );
}
