"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { FolderOpen, Plus } from "lucide-react";
import {
  PageHeader,
  Button,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
  Dialog,
  Input,
  Textarea,
  DataTable,
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import { ApiError, knowledge, type KnowledgeSource } from "@/lib/api";

export default function SourcesPage() {
  const { user } = useAuth();
  const toast = useToast();
  const router = useRouter();
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ display_name: "", description: "" });
  const [docCounts, setDocCounts] = useState<Record<string, number>>({});

  const load = useCallback(async () => {
    if (!user?.workspace_id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const list = await knowledge.listSources(user.workspace_id, true);
      setSources(list);
      const counts: Record<string, number> = {};
      await Promise.all(
        list.map(async (s) => {
          const docs = await knowledge.listDocuments(user.workspace_id!, {
            source_id: s.id,
            include_archived: true,
          });
          counts[s.id] = docs.length;
        }),
      );
      setDocCounts(counts);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to load sources");
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!user?.workspace_id) {
    return (
      <EmptyState
        icon={FolderOpen}
        title="No active workspace"
        description="Select a workspace to manage knowledge sources."
        actionLabel="Workspaces"
        onAction={() => router.push("/dashboard/workspaces")}
      />
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Sources"
        description="Knowledge sources bound to the active workspace."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" />
            Add source
          </Button>
        }
      />

      {loading ? (
        <LoadingSkeleton lines={5} />
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : sources.length === 0 ? (
        <EmptyState
          icon={FolderOpen}
          title="No sources yet"
          description="Create a manual upload source to begin ingesting documents."
          actionLabel="Add source"
          onAction={() => setOpen(true)}
        />
      ) : (
        <DataTable
          rows={sources}
          columns={[
            {
              key: "name",
              header: "Source",
              cell: (s) => (
                <div>
                  <p className="font-medium">{s.display_name}</p>
                  <p className="text-xs text-muted">{s.description || "—"}</p>
                </div>
              ),
            },
            {
              key: "type",
              header: "Type",
              cell: (s) => <span className="text-xs text-muted">{s.source_type}</span>,
            },
            {
              key: "docs",
              header: "Documents",
              cell: (s) => docCounts[s.id] ?? 0,
            },
            {
              key: "status",
              header: "Status",
              cell: (s) => (
                <StatusBadge tone={s.is_active ? "success" : "neutral"}>
                  {s.is_active ? "Active" : "Inactive"}
                </StatusBadge>
              ),
            },
            {
              key: "updated",
              header: "Updated",
              cell: (s) => (
                <span className="text-xs text-muted">
                  {new Date(s.updated_at).toLocaleString()}
                </span>
              ),
            },
            {
              key: "actions",
              header: "",
              cell: (s) => (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    try {
                      await knowledge.updateSource(user.workspace_id!, s.id, {
                        is_active: !s.is_active,
                      });
                      toast.success(s.is_active ? "Source deactivated" : "Source activated");
                      await load();
                    } catch (err) {
                      toast.error(
                        "Update failed",
                        err instanceof ApiError ? err.detail : "Error",
                      );
                    }
                  }}
                >
                  {s.is_active ? "Deactivate" : "Activate"}
                </Button>
              ),
            },
          ]}
        />
      )}

      <Dialog open={open} onClose={() => setOpen(false)} title="Add source">
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-muted">Name</label>
            <Input
              value={form.display_name}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">Description</label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              rows={3}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={creating}
              onClick={async () => {
                setCreating(true);
                try {
                  await knowledge.createSource(user.workspace_id!, {
                    source_type: "manual_upload",
                    display_name: form.display_name.trim(),
                    description: form.description.trim() || null,
                  });
                  toast.success("Source created");
                  setOpen(false);
                  setForm({ display_name: "", description: "" });
                  await load();
                } catch (err) {
                  toast.error(
                    "Create failed",
                    err instanceof ApiError ? err.detail : "Error",
                  );
                } finally {
                  setCreating(false);
                }
              }}
            >
              Create
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
