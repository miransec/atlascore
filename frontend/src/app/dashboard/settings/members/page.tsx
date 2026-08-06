"use client";

/**
 * Settings > Members — list, change role, remove org members.
 */

import { useEffect, useState, useCallback } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string | null;
  org_role: string | null;
  created_at: string;
}

const ORG_ROLES = [
  "owner",
  "administrator",
  "workflow_builder",
  "analyst",
  "operator",
  "viewer",
  "auditor",
];

export default function MembersPage() {
  const { user } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const adminRoles = ["owner", "administrator"];
  const isAdmin = user && adminRoles.includes(user.org_role ?? "");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { apiFetch } = await import("@/lib/api").then((m) => ({ apiFetch: m }));
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8100"}/api/v1/organisations/current/members`,
        {
          headers: {
            Authorization: `Bearer ${(await import("@/lib/api")).getAccessToken() ?? ""}`,
          },
          credentials: "include",
        },
      );
      if (!res.ok) throw new Error("Failed to load members.");
      setMembers(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function changeRole(userId: string, newRole: string) {
    setActionError(null);
    try {
      const token = (await import("@/lib/api")).getAccessToken();
      const csrf = document.cookie
        .split("; ")
        .find((r) => r.startsWith("csrf_token="))
        ?.split("=")[1];
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8100"}/api/v1/organisations/current/members/${userId}/role`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token ?? ""}`,
            ...(csrf ? { "X-CSRF-Token": csrf } : {}),
          },
          credentials: "include",
          body: JSON.stringify({ org_role: newRole }),
        },
      );
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d?.detail ?? "Failed to change role.");
      }
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  async function removeMember(userId: string) {
    if (!confirm("Remove this member from the organisation?")) return;
    setActionError(null);
    try {
      const token = (await import("@/lib/api")).getAccessToken();
      const csrf = document.cookie
        .split("; ")
        .find((r) => r.startsWith("csrf_token="))
        ?.split("=")[1];
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8100"}/api/v1/organisations/current/members/${userId}`,
        {
          method: "DELETE",
          headers: {
            Authorization: `Bearer ${token ?? ""}`,
            ...(csrf ? { "X-CSRF-Token": csrf } : {}),
          },
          credentials: "include",
        },
      );
      if (!res.ok && res.status !== 204) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d?.detail ?? "Failed to remove member.");
      }
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold text-gray-900">Members</h1>

      {error && (
        <p className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}
      {actionError && (
        <p className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">{actionError}</p>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <div className="overflow-hidden rounded border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Email</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Name</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Role</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Joined</th>
                {isAdmin && (
                  <th className="px-4 py-3 text-right font-medium text-gray-500">Actions</th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {members.map((m) => (
                <tr key={m.id}>
                  <td className="px-4 py-3 text-gray-900">{m.email}</td>
                  <td className="px-4 py-3 text-gray-500">{m.full_name ?? "—"}</td>
                  <td className="px-4 py-3">
                    {isAdmin && m.user_id !== user?.user_id ? (
                      <select
                        value={m.org_role ?? ""}
                        onChange={(e) => changeRole(m.user_id, e.target.value)}
                        className="rounded border border-gray-200 px-2 py-1 text-sm"
                      >
                        {ORG_ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
                        {m.org_role ?? "—"}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {new Date(m.created_at).toLocaleDateString()}
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-right">
                      {m.user_id !== user?.user_id && m.org_role !== "owner" && (
                        <button
                          onClick={() => removeMember(m.user_id)}
                          className="text-xs text-red-600 hover:underline"
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {members.length === 0 && (
            <p className="px-4 py-6 text-center text-sm text-gray-400">No members found.</p>
          )}
        </div>
      )}
    </div>
  );
}
