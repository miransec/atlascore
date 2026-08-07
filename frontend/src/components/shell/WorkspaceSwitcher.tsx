"use client";

import { Check, ChevronsUpDown, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Dropdown, DropdownItem } from "@/components/ui/dropdown";
import { StatusBadge } from "@/components/ui/status-badge";
import { SkeletonBlock } from "@/components/ui/loading-skeleton";
import type { Workspace } from "@/lib/api";
import { cn } from "@/lib/cn";

export function WorkspaceSwitcher({
  workspaces,
  currentId,
  loading,
  onSwitch,
  onCreate,
}: {
  workspaces: Workspace[];
  currentId: string | null;
  loading?: boolean;
  onSwitch: (id: string) => void;
  onCreate: () => void;
}) {
  const current = workspaces.find((w) => w.id === currentId) ?? null;
  const [pending, setPending] = useState(false);

  useEffect(() => {
    setPending(false);
  }, [currentId]);

  if (loading) {
    return <SkeletonBlock className="h-9 w-48" />;
  }

  return (
    <Dropdown
      align="left"
      trigger={
        <button
          type="button"
          className="inline-flex h-9 max-w-[240px] items-center gap-2 rounded-md border border-border bg-surface px-3 text-sm hover:bg-surface-raised"
          aria-label="Switch workspace"
        >
          <span className="truncate font-medium">
            {current?.display_name ?? "Select workspace"}
          </span>
          {pending ? <StatusBadge tone="accent" pulse>Switching</StatusBadge> : null}
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted" />
        </button>
      }
    >
      <div className="max-h-64 overflow-y-auto py-1">
        {workspaces.length === 0 ? (
          <p className="px-2.5 py-2 text-xs text-muted">No workspaces yet</p>
        ) : (
          workspaces.map((ws) => (
            <DropdownItem
              key={ws.id}
              onClick={() => {
                if (ws.id === currentId) return;
                setPending(true);
                onSwitch(ws.id);
              }}
            >
              <Check
                className={cn(
                  "h-3.5 w-3.5",
                  ws.id === currentId ? "opacity-100 text-accent-hover" : "opacity-0",
                )}
              />
              <span className="truncate">{ws.display_name}</span>
            </DropdownItem>
          ))
        )}
      </div>
      <div className="border-t border-border p-1">
        <DropdownItem onClick={onCreate}>
          <Plus className="h-3.5 w-3.5" />
          Create workspace
        </DropdownItem>
      </div>
    </Dropdown>
  );
}
