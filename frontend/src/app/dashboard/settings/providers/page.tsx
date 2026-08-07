"use client";

import { useEffect, useState } from "react";
import { Bot } from "lucide-react";
import {
  PageHeader,
  StatusBadge,
  LoadingSkeleton,
  EmptyState,
  ErrorState,
} from "@/components/ui";
import { ApiError, getHealth, type HealthResponse } from "@/lib/api";

export default function ProvidersSettingsPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((err) =>
        setError(err instanceof ApiError ? err.detail : "Provider status unavailable"),
      )
      .finally(() => setLoading(false));
  }, []);

  const demo =
    health?.demo_mode === true || health?.demo_mode === "true";

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="AI Providers"
        description="Provider configuration is server-managed. API keys are never shown in the UI."
      />
      {loading ? (
        <LoadingSkeleton lines={4} />
      ) : error ? (
        <ErrorState description={error} />
      ) : !health ? (
        <EmptyState icon={Bot} title="No provider status" />
      ) : (
        <div className="surface-card max-w-xl space-y-4 p-5">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">Answer provider</span>
            <StatusBadge tone="accent">{health.answer_provider}</StatusBadge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">Demo mode</span>
            <StatusBadge tone={demo ? "warning" : "success"}>
              {demo ? "Forced deterministic" : "Off"}
            </StatusBadge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">Health</span>
            <StatusBadge tone="success" pulse>
              {health.status}
            </StatusBadge>
          </div>
          {health.version ? (
            <p className="text-xs text-muted">Backend version {health.version}</p>
          ) : null}
          <p className="text-xs text-muted">
            OpenAI-compatible base URLs (when configured) are applied server-side via
            OPENAI_BASE_URL. Secrets remain in environment configuration only.
          </p>
        </div>
      )}
    </div>
  );
}
