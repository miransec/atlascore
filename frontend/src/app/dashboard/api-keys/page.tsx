"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Shield } from "lucide-react";
import {
  PageHeader,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  DataTable,
  StatusBadge,
  Button,
  ConfirmDialog,
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { ApiError, serviceAccounts, type ApiKey, type ServiceAccount } from "@/lib/api";

export default function ApiKeysPage() {
  const toast = useToast();
  const [accounts, setAccounts] = useState<ServiceAccount[]>([]);
  const [keys, setKeys] = useState<(ApiKey & { sa_name: string })[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<{ saId: string; keyId: string } | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const sas = await serviceAccounts.list({ page_size: 50 });
      setAccounts(sas.items);
      const all: (ApiKey & { sa_name: string })[] = [];
      for (const sa of sas.items) {
        const page = await serviceAccounts.listApiKeys(sa.id, { page_size: 50 });
        for (const k of page.items) {
          all.push({ ...k, sa_name: sa.name });
        }
      }
      setKeys(all);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to load API keys");
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
        title="API Keys"
        description="Keys are shown by prefix only. Raw secrets appear once at creation under Service Accounts."
      />

      {loading ? (
        <LoadingSkeleton lines={4} />
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : keys.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="No API keys"
          description={
            accounts.length === 0
              ? "Create a service account first, then issue a key."
              : "Issue a key from a service account."
          }
        />
      ) : (
        <DataTable
          rows={keys}
          columns={[
            {
              key: "name",
              header: "Key",
              cell: (k) => (
                <div>
                  <p className="font-medium">{k.name}</p>
                  <p className="font-mono text-xs text-muted">{k.key_prefix}…</p>
                </div>
              ),
            },
            {
              key: "sa",
              header: "Service account",
              cell: (k) => k.sa_name,
            },
            {
              key: "status",
              header: "Status",
              cell: (k) => (
                <StatusBadge tone={k.is_active ? "success" : "danger"}>
                  {k.is_active ? "Active" : "Revoked"}
                </StatusBadge>
              ),
            },
            {
              key: "created",
              header: "Created",
              cell: (k) => (
                <span className="text-xs text-muted">
                  {new Date(k.created_at).toLocaleString()}
                </span>
              ),
            },
            {
              key: "actions",
              header: "",
              cell: (k) =>
                k.is_active ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      setRevokeTarget({ saId: k.service_account_id, keyId: k.id })
                    }
                  >
                    Revoke
                  </Button>
                ) : null,
            },
          ]}
        />
      )}

      {accounts.length === 0 && !loading ? (
        <p className="mt-4 flex items-center gap-2 text-xs text-muted">
          <Shield className="h-3.5 w-3.5" />
          Manage service accounts to create keys.
        </p>
      ) : null}

      <ConfirmDialog
        open={Boolean(revokeTarget)}
        onClose={() => setRevokeTarget(null)}
        title="Revoke API key?"
        description="This cannot be undone. Integrations using the key will fail."
        confirmLabel="Revoke"
        danger
        onConfirm={async () => {
          if (!revokeTarget) return;
          try {
            await serviceAccounts.revokeApiKey(revokeTarget.saId, revokeTarget.keyId);
            toast.success("Key revoked");
            setRevokeTarget(null);
            await load();
          } catch (err) {
            toast.error("Revoke failed", err instanceof ApiError ? err.detail : "Error");
          }
        }}
      />
    </div>
  );
}
