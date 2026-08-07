"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Boxes, Plus } from "lucide-react";
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
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import { ApiError, workspaces as workspacesApi, type Workspace } from "@/lib/api";

function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63);
}

export default function WorkspacesPage() {
  const { user, switchWorkspace } = useAuth();
  const toast = useToast();
  const router = useRouter();
  const [list, setList] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const [form, setForm] = useState({ display_name: "", slug: "", description: "" });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setList(await workspacesApi.list());
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to load workspaces");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate() {
    setCreating(true);
    try {
      const ws = await workspacesApi.create({
        display_name: form.display_name.trim(),
        slug: form.slug.trim(),
        description: form.description.trim() || null,
      });
      await switchWorkspace(ws.id);
      toast.success("Workspace created", "You are now in " + ws.display_name);
      setOpen(false);
      setForm({ display_name: "", slug: "", description: "" });
      router.push("/dashboard");
    } catch (err) {
      toast.error("Create failed", err instanceof ApiError ? err.detail : "Unknown error");
    } finally {
      setCreating(false);
    }
  }

  async function onSwitch(id: string) {
    if (id === user?.workspace_id) return;
    setSwitching(id);
    try {
      await switchWorkspace(id);
      toast.success("Workspace switched");
      await load();
    } catch (err) {
      toast.error("Switch failed", err instanceof ApiError ? err.detail : "Unknown error");
    } finally {
      setSwitching(null);
    }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Workspaces"
        description="Isolate knowledge and grounded answering per team or project."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" />
            Create workspace
          </Button>
        }
      />

      {loading ? (
        <LoadingSkeleton lines={5} />
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : list.length === 0 ? (
        <EmptyState
          icon={Boxes}
          title="No workspaces yet"
          description="Create your first workspace to start ingesting knowledge."
          actionLabel="Create workspace"
          onAction={() => setOpen(true)}
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {list.map((ws) => {
            const active = ws.id === user?.workspace_id;
            return (
              <div
                key={ws.id}
                className="surface-card flex flex-col p-5 transition hover:-translate-y-0.5 hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="font-semibold text-foreground">{ws.display_name}</h3>
                    <p className="mt-0.5 font-mono text-xs text-muted">{ws.slug}</p>
                  </div>
                  {active ? <StatusBadge tone="accent">Current</StatusBadge> : null}
                </div>
                <p className="mt-3 line-clamp-2 flex-1 text-sm text-muted">
                  {ws.description || "No description"}
                </p>
                <div className="mt-4 flex gap-2">
                  <Button
                    size="sm"
                    variant={active ? "secondary" : "primary"}
                    loading={switching === ws.id}
                    disabled={active}
                    onClick={() => onSwitch(ws.id)}
                  >
                    {active ? "Active" : "Switch"}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Create workspace"
        description="You become the workspace administrator automatically."
      >
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-muted">Display name</label>
            <Input
              value={form.display_name}
              onChange={(e) => {
                const display_name = e.target.value;
                setForm((f) => ({
                  ...f,
                  display_name,
                  slug:
                    f.slug && f.slug !== slugify(f.display_name)
                      ? f.slug
                      : slugify(display_name),
                }));
              }}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">Slug</label>
            <Input
              value={form.slug}
              onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))}
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
            <Button loading={creating} onClick={onCreate}>
              Create & switch
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
