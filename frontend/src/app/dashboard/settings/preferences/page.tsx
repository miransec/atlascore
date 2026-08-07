"use client";

import { useEffect, useState } from "react";
import { PageHeader, Button, StatusBadge } from "@/components/ui";
import { useToast } from "@/components/ui/toast";

const SIDEBAR_KEY = "atlascore.sidebar.collapsed";

export default function PreferencesPage() {
  const toast = useToast();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(window.localStorage.getItem(SIDEBAR_KEY) === "1");
  }, []);

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Preferences"
        description="Local UI preferences only. These settings never leave your browser."
      />
      <div className="surface-card max-w-xl space-y-4 p-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium">Collapsed sidebar</p>
            <p className="text-xs text-muted">Remember the compact navigation state.</p>
          </div>
          <StatusBadge tone={collapsed ? "accent" : "neutral"}>
            {collapsed ? "On" : "Off"}
          </StatusBadge>
        </div>
        <Button
          variant="secondary"
          onClick={() => {
            const next = !collapsed;
            setCollapsed(next);
            window.localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
            toast.success("Preference saved", "Reload or toggle the sidebar to apply.");
          }}
        >
          Toggle default
        </Button>
      </div>
    </div>
  );
}
