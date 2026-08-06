"use client";

/**
 * Settings > Service Accounts — create service accounts, manage API keys.
 *
 * Security:
 * - raw_key is held in ephemeral React state only.
 * - It is displayed once in a dismissible banner.
 * - It is NEVER written to localStorage, sessionStorage, URL params,
 *   or console logs.
 * - Dismissing the banner sets the state to null — the key is gone.
 */

import { useEffect, useState, useCallback } from "react";
import {
  serviceAccounts,
  ServiceAccount,
  ApiKey,
  ApiKeyCreated,
  PaginatedApiKeys,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const ALL_SCOPES = [
  "invitation:create",
  "invitation:revoke",
  "invitation:list",
  "team:create",
  "team:update",
  "team:delete",
  "team:read",
  "team:member:manage",
  "service_account:create",
  "service_account:manage",
  "service_account:read",
  "api_key:create",
  "api_key:revoke",
  "api_key:list",
  "org:read",
  "workspace:read",
  "workflow:read",
  "workflow:run",
  "audit:read",
];

export default function ServiceAccountsPage() {
  const { user } = useAuth();
  const [saList, setSaList] = useState<ServiceAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedSa, setSelectedSa] = useState<ServiceAccount | null>(null);
  const [keys, setKeys] = useState<ApiKey[]>([]);

  // Create SA form
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  // Create API key form
  const [keyName, setKeyName] = useState("");
  const [keyScopes, setKeyScopes] = useState<string[]>(["org:read"]);
  const [keyExpiry, setKeyExpiry] = useState<string>("");
  const [creatingKey, setCreatingKey] = useState(false);

  // One-time raw key display
  const [rawKey, setRawKey] = useState<string | null>(null);
  const [rawKeyWarning, setRawKeyWarning] = useState<string>("");

  const adminRoles = ["owner", "administrator"];
  const isAdmin = user && adminRoles.includes(user.org_role ?? "");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await serviceAccounts.list({ page: 1, page_size: 100 });
      setSaList(data.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function loadKeys(saId: string) {
    try {
      const data: PaginatedApiKeys = await serviceAccounts.listApiKeys(saId);
      setKeys(data.items);
    } catch {
      setKeys([]);
    }
  }

  async function selectSa(sa: ServiceAccount) {
    setSelectedSa(sa);
    await loadKeys(sa.id);
  }

  async function handleCreateSa(ev: React.FormEvent) {
    ev.preventDefault();
    setActionError(null);
    setCreating(true);
    try {
      await serviceAccounts.create({ name: newName, description: newDesc || null });
      setNewName("");
      setNewDesc("");
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(sa: ServiceAccount) {
    setActionError(null);
    try {
      if (sa.is_active) {
        await serviceAccounts.disable(sa.id);
      } else {
        await serviceAccounts.enable(sa.id);
      }
      await load();
      if (selectedSa?.id === sa.id) {
        const updated = saList.find((s) => s.id === sa.id);
        if (updated) setSelectedSa({ ...updated, is_active: !updated.is_active });
      }
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  async function handleCreateKey(ev: React.FormEvent) {
    ev.preventDefault();
    if (!selectedSa) return;
    setActionError(null);
    setCreatingKey(true);
    try {
      const result: ApiKeyCreated = await serviceAccounts.createApiKey(selectedSa.id, {
        name: keyName,
        scopes: keyScopes,
        expires_in_days: keyExpiry ? parseInt(keyExpiry) : null,
      });
      // Display raw key ONCE — never persist
      setRawKey(result.raw_key);
      setRawKeyWarning(result.warning);
      setKeyName("");
      setKeyScopes(["org:read"]);
      setKeyExpiry("");
      await loadKeys(selectedSa.id);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    } finally {
      setCreatingKey(false);
    }
  }

  async function handleRevokeKey(keyId: string) {
    if (!selectedSa) return;
    if (!confirm("Revoke this API key? This cannot be undone.")) return;
    setActionError(null);
    try {
      await serviceAccounts.revokeApiKey(selectedSa.id, keyId);
      await loadKeys(selectedSa.id);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  async function handleRotateKey(keyId: string) {
    if (!selectedSa) return;
    if (!confirm("Rotate this API key? The old key will stop working immediately.")) return;
    setActionError(null);
    try {
      const result: ApiKeyCreated = await serviceAccounts.rotateApiKey(selectedSa.id, keyId);
      setRawKey(result.raw_key);
      setRawKeyWarning(result.warning);
      await loadKeys(selectedSa.id);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  function toggleScope(scope: string) {
    setKeyScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold text-gray-900">Service Accounts</h1>

      {/* One-time raw key banner */}
      {rawKey && (
        <div className="mb-6 rounded border border-amber-300 bg-amber-50 p-4">
          <p className="mb-1 text-sm font-semibold text-amber-900">⚠ New API Key — Copy Now</p>
          <p className="mb-2 text-xs text-amber-700">{rawKeyWarning}</p>
          <code className="block break-all rounded bg-white px-3 py-2 font-mono text-xs text-gray-800 shadow-inner">
            {rawKey}
          </code>
          <button
            className="mt-2 text-xs text-amber-700 underline"
            onClick={() => {
              setRawKey(null);
              setRawKeyWarning("");
            }}
          >
            I have copied it — dismiss
          </button>
        </div>
      )}

      {error && (
        <p className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}
      {actionError && (
        <p className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">{actionError}</p>
      )}

      <div className="flex gap-6">
        {/* Service account list */}
        <div className="flex-1">
          {isAdmin && (
            <form onSubmit={handleCreateSa} className="mb-4 flex gap-2">
              <input
                required
                placeholder="Account name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
              />
              <input
                placeholder="Description (optional)"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
              />
              <button
                type="submit"
                disabled={creating}
                className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {creating ? "Creating…" : "Create"}
              </button>
            </form>
          )}

          {loading ? (
            <p className="text-sm text-gray-500">Loading…</p>
          ) : (
            <div className="overflow-hidden rounded border border-gray-200">
              <table className="min-w-full divide-y divide-gray-200 text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium text-gray-500">Name</th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500">Status</th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500">Last used</th>
                    {isAdmin && (
                      <th className="px-4 py-3 text-right font-medium text-gray-500">Actions</th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {saList.map((sa) => (
                    <tr
                      key={sa.id}
                      className={`cursor-pointer ${selectedSa?.id === sa.id ? "bg-indigo-50" : "hover:bg-gray-50"}`}
                      onClick={() => selectSa(sa)}
                    >
                      <td className="px-4 py-3 font-medium text-gray-900">
                        {sa.name}
                        {sa.description && (
                          <span className="ml-2 text-xs text-gray-400">{sa.description}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${
                            sa.is_active
                              ? "bg-green-100 text-green-700"
                              : "bg-gray-100 text-gray-500"
                          }`}
                        >
                          {sa.is_active ? "Active" : "Disabled"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-500">
                        {sa.last_used_at
                          ? new Date(sa.last_used_at).toLocaleDateString()
                          : "Never"}
                      </td>
                      {isAdmin && (
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleToggleActive(sa);
                            }}
                            className="text-xs text-indigo-600 hover:underline"
                          >
                            {sa.is_active ? "Disable" : "Enable"}
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              {saList.length === 0 && (
                <p className="px-4 py-6 text-center text-sm text-gray-400">
                  No service accounts.
                </p>
              )}
            </div>
          )}
        </div>

        {/* API keys panel */}
        {selectedSa && (
          <div className="w-96 shrink-0 rounded border border-gray-200 p-4">
            <h2 className="mb-3 text-sm font-semibold text-gray-700">
              API Keys — {selectedSa.name}
            </h2>

            {isAdmin && selectedSa.is_active && (
              <form onSubmit={handleCreateKey} className="mb-4 space-y-3">
                <input
                  required
                  placeholder="Key name"
                  value={keyName}
                  onChange={(e) => setKeyName(e.target.value)}
                  className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
                />
                <div>
                  <p className="mb-1 text-xs font-medium text-gray-500">Scopes</p>
                  <div className="grid grid-cols-2 gap-1">
                    {ALL_SCOPES.map((s) => (
                      <label key={s} className="flex items-center gap-1 text-xs text-gray-600">
                        <input
                          type="checkbox"
                          checked={keyScopes.includes(s)}
                          onChange={() => toggleScope(s)}
                        />
                        {s}
                      </label>
                    ))}
                  </div>
                </div>
                <input
                  type="number"
                  placeholder="Expires in days (blank = never)"
                  value={keyExpiry}
                  onChange={(e) => setKeyExpiry(e.target.value)}
                  min={1}
                  max={3650}
                  className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
                />
                <button
                  type="submit"
                  disabled={creatingKey || keyScopes.length === 0}
                  className="w-full rounded bg-indigo-600 py-1 text-xs text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {creatingKey ? "Creating…" : "Create API Key"}
                </button>
              </form>
            )}

            <ul className="divide-y divide-gray-100 text-xs">
              {keys.map((k) => (
                <li key={k.id} className="py-2">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="font-medium text-gray-800">{k.name}</p>
                      <p className="text-gray-400">
                        Prefix:{" "}
                        <span className="font-mono">atk_{k.key_prefix}_…</span>
                      </p>
                      <p className="text-gray-400">
                        {k.is_active
                          ? k.expires_at
                            ? `Expires ${new Date(k.expires_at).toLocaleDateString()}`
                            : "No expiry"
                          : k.revoked_at
                            ? `Revoked ${new Date(k.revoked_at).toLocaleDateString()}`
                            : "Inactive"}
                      </p>
                    </div>
                    {isAdmin && k.is_active && (
                      <div className="flex flex-col gap-1 text-right">
                        <button
                          onClick={() => handleRotateKey(k.id)}
                          className="text-indigo-600 hover:underline"
                        >
                          Rotate
                        </button>
                        <button
                          onClick={() => handleRevokeKey(k.id)}
                          className="text-red-500 hover:underline"
                        >
                          Revoke
                        </button>
                      </div>
                    )}
                  </div>
                </li>
              ))}
              {keys.length === 0 && (
                <li className="py-4 text-center text-gray-400">No API keys.</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
