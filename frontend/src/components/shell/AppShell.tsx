"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { CommandPalette } from "./CommandPalette";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  auth,
  getHealth,
  workspaces as workspacesApi,
  type Workspace,
} from "@/lib/api";
import { cn } from "@/lib/cn";

const PREF_KEY = "atlascore.sidebar.collapsed";

function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const toast = useToast();
  const { user, signOut, switchWorkspace } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [wsList, setWsList] = useState<Workspace[]>([]);
  const [wsLoading, setWsLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ display_name: "", slug: "", description: "" });
  const [providerLabel, setProviderLabel] = useState("checking");
  const [providerHealthy, setProviderHealthy] = useState(true);

  const loadWorkspaces = useCallback(async () => {
    setWsLoading(true);
    try {
      const list = await workspacesApi.list();
      setWsList(list);
    } catch {
      setWsList([]);
    } finally {
      setWsLoading(false);
    }
  }, []);

  useEffect(() => {
    const stored = window.localStorage.getItem(PREF_KEY);
    if (stored === "1") setCollapsed(true);
  }, []);

  useEffect(() => {
    void loadWorkspaces();
    void getHealth()
      .then((h) => {
        const demo = h.demo_mode === "true" || h.demo_mode === true;
        setProviderLabel(demo ? `${h.answer_provider} · demo` : h.answer_provider);
        setProviderHealthy(true);
      })
      .catch(() => {
        setProviderLabel("unavailable");
        setProviderHealthy(false);
      });
  }, [loadWorkspaces, user?.organisation_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function toggleCollapsed() {
    setCollapsed((v) => {
      const next = !v;
      window.localStorage.setItem(PREF_KEY, next ? "1" : "0");
      return next;
    });
  }

  async function handleLogout() {
    try {
      await auth.logout();
    } catch {
      /* still clear local session */
    } finally {
      signOut();
      router.replace("/login");
    }
  }

  async function handleSwitch(id: string) {
    try {
      await switchWorkspace(id);
      toast.success("Workspace switched");
      router.refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : "Could not switch workspace";
      toast.error("Switch failed", String(msg));
    }
  }

  async function handleCreate() {
    if (!form.display_name.trim() || !form.slug.trim()) return;
    setCreating(true);
    try {
      const ws = await workspacesApi.create({
        display_name: form.display_name.trim(),
        slug: form.slug.trim(),
        description: form.description.trim() || null,
      });
      await switchWorkspace(ws.id);
      await loadWorkspaces();
      setCreateOpen(false);
      setForm({ display_name: "", slug: "", description: "" });
      toast.success("Workspace created", ws.display_name);
      router.push("/dashboard");
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : "Create failed";
      toast.error("Could not create workspace", String(msg));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Sidebar collapsed={collapsed} onToggle={toggleCollapsed} onLogout={handleLogout} />
      <div
        className={cn(
          "min-h-screen transition-[padding] duration-200",
          collapsed ? "pl-[var(--sidebar-collapsed)]" : "pl-[var(--sidebar-width)]",
        )}
      >
        <Topbar
          workspaces={wsList}
          workspacesLoading={wsLoading}
          onSwitchWorkspace={handleSwitch}
          onCreateWorkspace={() => setCreateOpen(true)}
          onOpenCommand={() => setPaletteOpen(true)}
          providerLabel={providerLabel}
          providerHealthy={providerHealthy}
        />
        <main className="px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create workspace"
        description="Workspaces isolate knowledge, search, and grounded answers."
      >
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Display name</label>
            <Input
              value={form.display_name}
              onChange={(e) => {
                const display_name = e.target.value;
                setForm((f) => ({
                  ...f,
                  display_name,
                  slug: f.slug && f.slug !== slugify(f.display_name) ? f.slug : slugify(display_name),
                }));
              }}
              placeholder="Engineering"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Slug</label>
            <Input
              value={form.slug}
              onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))}
              placeholder="engineering"
              pattern="^[a-z0-9-]+$"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Description</label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="Optional"
              rows={3}
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button loading={creating} onClick={handleCreate}>
              Create & switch
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
