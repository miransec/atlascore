"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity } from "lucide-react";
import {
  PageHeader,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
  DataTable,
} from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { ApiError, knowledge, type KnowledgeIngestionJob } from "@/lib/api";

export default function ActivityPage() {
  const { user } = useAuth();
  const [jobs, setJobs] = useState<KnowledgeIngestionJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user?.workspace_id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const list = await knowledge.listJobs(user.workspace_id);
      setJobs(list.slice(0, 50));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to load activity");
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Activity"
        description="Recent ingestion jobs for the active workspace. Audit event timelines are not exposed via API in this release."
      />
      {!user?.workspace_id ? (
        <EmptyState icon={Activity} title="No active workspace" />
      ) : loading ? (
        <LoadingSkeleton lines={5} />
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="No recent jobs"
          description="Upload documents to see ingestion activity here."
        />
      ) : (
        <DataTable
          rows={jobs}
          columns={[
            {
              key: "status",
              header: "Status",
              cell: (j) => (
                <StatusBadge
                  tone={
                    j.status === "succeeded"
                      ? "success"
                      : j.status === "failed"
                        ? "danger"
                        : "accent"
                  }
                  pulse={j.status === "running" || j.status === "queued"}
                >
                  {j.status}
                </StatusBadge>
              ),
            },
            {
              key: "doc",
              header: "Document",
              cell: (j) => <span className="font-mono text-xs">{j.document_id.slice(0, 8)}…</span>,
            },
            {
              key: "created",
              header: "Created",
              cell: (j) => (
                <span className="text-xs text-muted">
                  {new Date(j.created_at).toLocaleString()}
                </span>
              ),
            },
            {
              key: "error",
              header: "Detail",
              cell: (j) => (
                <span className="text-xs text-muted">{j.error_message ?? "—"}</span>
              ),
            },
          ]}
        />
      )}
    </div>
  );
}
