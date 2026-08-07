"use client";

import { useCallback, useEffect, useState } from "react";
import { Shield, Plus } from "lucide-react";
import {
  PageHeader,
  Button,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  DataTable,
  StatusBadge,
  Dialog,
  Input,
  Textarea,
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import { ApiError, serviceAccounts, type ServiceAccount } from "@/lib/api";

export default function ServiceAccountsPage() {
  const { user } = useAuth();
  const toast = useToast();
  const [items, setItems] = useState<ServiceAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState<ServiceAccount | null>(null);
  const [rawKey, setRawKey] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", description: "" });
  const [keyName, setKeyName] = useState("default");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await serviceAccounts.list({ page_size: 50 });
      setItems(res.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to load service accounts");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Service Accounts"
        description="Non-human principals for API access. Keys are shown once at creation."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" />
            Create
          </Button>
        }
      />

      {loading ? (
        <LoadingSkeleton lines={4} />
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Shield}
          title="No service accounts"
          actionLabel="Create"
          onAction={() => setOpen(true)}
        />
      ) : (
        <DataTable
          rows={items}
          columns={[
            {
              key: "name",
              header: "Account",
              cell: (sa) => (
                <div>
                  <p className="font-medium">{sa.name}</p>
                  <p className="text-xs text-muted">{sa.description || "—"}</p>
                </div>
              ),
            },
            {
              key: "status",
              header: "Status",
              cell: (sa) => (
                <StatusBadge tone={sa.is_active ? "success" : "danger"}>
                  {sa.is_active ? "Active" : "Disabled"}
                </StatusBadge>
              ),
            },
            {
              key: "actions",
              header: "",
              cell: (sa) => (
                <div className="flex gap-1">
                  <Button size="sm" variant="secondary" onClick={() => setKeyOpen(sa)}>
                    Issue key
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => {
                      try {
                        if (sa.is_active) await serviceAccounts.disable(sa.id);
                        else await serviceAccounts.enable(sa.id);
                        await load();
                      } catch (err) {
                        toast.error(
                          "Update failed",
                          err instanceof ApiError ? err.detail : "Error",
                        );
                      }
                    }}
                  >
                    {sa.is_active ? "Disable" : "Enable"}
                  </Button>
                </div>
              ),
            },
          ]}
        />
      )}

      <Dialog open={open} onClose={() => setOpen(false)} title="Create service account">
        <div className="space-y-3">
          <Input
            placeholder="Name"
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
              loading={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await serviceAccounts.create({
                    name: form.name.trim(),
                    description: form.description.trim() || null,
                    workspace_id: user?.workspace_id ?? null,
                  });
                  toast.success("Service account created");
                  setOpen(false);
                  setForm({ name: "", description: "" });
                  await load();
                } catch (err) {
                  toast.error(
                    "Create failed",
                    err instanceof ApiError ? err.detail : "Error",
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              Create
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(keyOpen)}
        onClose={() => {
          setKeyOpen(null);
          setRawKey(null);
          setKeyName("default");
        }}
        title="Issue API key"
        description="The raw key is shown once. Store it securely."
      >
        {rawKey ? (
          <div className="space-y-3">
            <code className="block break-all rounded-md border border-border bg-surface p-3 text-xs">
              {rawKey}
            </code>
            <Button
              onClick={async () => {
                await navigator.clipboard.writeText(rawKey);
                toast.success("Key copied");
              }}
            >
              Copy key
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <Input value={keyName} onChange={(e) => setKeyName(e.target.value)} />
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setKeyOpen(null)}>
                Cancel
              </Button>
              <Button
                loading={busy}
                onClick={async () => {
                  if (!keyOpen) return;
                  setBusy(true);
                  try {
                    const created = await serviceAccounts.createApiKey(keyOpen.id, {
                      name: keyName.trim() || "default",
                      scopes: ["knowledge:read"],
                    });
                    setRawKey(created.raw_key);
                    toast.success("API key created");
                  } catch (err) {
                    toast.error(
                      "Key creation failed",
                      err instanceof ApiError ? err.detail : "Error",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Create key
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
