"use client";

import { useCallback, useEffect, useState } from "react";
import { UsersRound, Plus } from "lucide-react";
import {
  PageHeader,
  Button,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  DataTable,
  Dialog,
  Input,
  Textarea,
  ConfirmDialog,
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import { ApiError, teams as teamsApi, type Team } from "@/lib/api";

export default function TeamsPage() {
  const { user } = useAuth();
  const toast = useToast();
  const [items, setItems] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", description: "" });
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await teamsApi.list({
        workspace_id: user?.workspace_id ?? undefined,
        page_size: 50,
      });
      setItems(res.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to load teams");
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
        title="Teams"
        description="Group people for collaboration within the organisation."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" />
            Create team
          </Button>
        }
      />

      {loading ? (
        <LoadingSkeleton lines={4} />
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={UsersRound}
          title="No teams yet"
          actionLabel="Create team"
          onAction={() => setOpen(true)}
        />
      ) : (
        <DataTable
          rows={items}
          columns={[
            {
              key: "name",
              header: "Team",
              cell: (t) => (
                <div>
                  <p className="font-medium">{t.name}</p>
                  <p className="text-xs text-muted">{t.description || "—"}</p>
                </div>
              ),
            },
            {
              key: "created",
              header: "Created",
              cell: (t) => (
                <span className="text-xs text-muted">
                  {new Date(t.created_at).toLocaleDateString()}
                </span>
              ),
            },
            {
              key: "actions",
              header: "",
              cell: (t) => (
                <Button size="sm" variant="ghost" onClick={() => setDeleteId(t.id)}>
                  Delete
                </Button>
              ),
            },
          ]}
        />
      )}

      <Dialog open={open} onClose={() => setOpen(false)} title="Create team">
        <div className="space-y-3">
          <Input
            placeholder="Team name"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
          <Textarea
            placeholder="Description"
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={creating}
              onClick={async () => {
                setCreating(true);
                try {
                  await teamsApi.create({
                    name: form.name.trim(),
                    description: form.description.trim() || null,
                    workspace_id: user?.workspace_id ?? null,
                  });
                  toast.success("Team created");
                  setOpen(false);
                  setForm({ name: "", description: "" });
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

      <ConfirmDialog
        open={Boolean(deleteId)}
        onClose={() => setDeleteId(null)}
        title="Delete team?"
        description="This removes the team and its memberships."
        confirmLabel="Delete"
        danger
        onConfirm={async () => {
          if (!deleteId) return;
          try {
            await teamsApi.delete(deleteId);
            toast.success("Team deleted");
            setDeleteId(null);
            await load();
          } catch (err) {
            toast.error("Delete failed", err instanceof ApiError ? err.detail : "Error");
          }
        }}
      />
    </div>
  );
}
