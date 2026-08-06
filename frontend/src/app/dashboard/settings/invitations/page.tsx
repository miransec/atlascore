"use client";

/**
 * Settings > Invitations — send, list, revoke invitations.
 *
 * Security note: raw_token is displayed in a one-time dismissible banner.
 * It is NEVER stored in state that persists beyond the component lifecycle,
 * never written to localStorage/sessionStorage, never logged to console.
 */

import { useEffect, useState, useCallback } from "react";
import { invitations, Invitation, InvitationCreated } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const ORG_ROLES_INVITABLE = [
  "administrator",
  "workflow_builder",
  "analyst",
  "operator",
  "viewer",
  "auditor",
];

export default function InvitationsPage() {
  const { user } = useAuth();
  const [list, setList] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Create form
  const [email, setEmail] = useState("");
  const [orgRole, setOrgRole] = useState("viewer");
  const [expiresHours, setExpiresHours] = useState(72);
  const [creating, setCreating] = useState(false);

  // One-time token display — never stored persistently
  const [newToken, setNewToken] = useState<string | null>(null);
  const [newEmail, setNewEmail] = useState<string | null>(null);

  const adminRoles = ["owner", "administrator"];
  const isAdmin = user && adminRoles.includes(user.org_role ?? "");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await invitations.list({ active_only: false });
      setList(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(ev: React.FormEvent) {
    ev.preventDefault();
    setActionError(null);
    setNewToken(null);
    setCreating(true);
    try {
      const result: InvitationCreated = await invitations.create({
        invited_email: email,
        org_role: orgRole,
        expires_in_hours: expiresHours,
      });
      // Display token once — clear form
      setNewToken(result.raw_token);
      setNewEmail(result.invitation.invited_email);
      setEmail("");
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(id: string) {
    if (!confirm("Revoke this invitation?")) return;
    setActionError(null);
    try {
      await invitations.revoke(id);
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  function statusBadge(inv: Invitation) {
    if (inv.accepted_at) return <span className="badge-green">Accepted</span>;
    if (inv.revoked_at) return <span className="badge-red">Revoked</span>;
    if (new Date(inv.expires_at) < new Date()) return <span className="badge-gray">Expired</span>;
    return <span className="badge-blue">Pending</span>;
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold text-gray-900">Invitations</h1>

      {/* One-time token banner */}
      {newToken && (
        <div className="mb-6 rounded border border-amber-300 bg-amber-50 p-4">
          <p className="mb-1 text-sm font-semibold text-amber-900">
            Invitation sent to {newEmail}
          </p>
          <p className="mb-2 text-xs text-amber-700">
            This token will not be shown again. Share it with the invitee via a secure channel.
          </p>
          <code className="block break-all rounded bg-white px-3 py-2 font-mono text-xs text-gray-800 shadow-inner">
            {newToken}
          </code>
          <button
            className="mt-2 text-xs text-amber-700 underline"
            onClick={() => setNewToken(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {error && (
        <p className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}
      {actionError && (
        <p className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">{actionError}</p>
      )}

      {isAdmin && (
        <form onSubmit={handleCreate} className="mb-8 rounded border border-gray-200 bg-gray-50 p-5">
          <h2 className="mb-4 text-sm font-semibold text-gray-700">Send Invitation</h2>
          <div className="flex flex-wrap gap-4">
            <input
              type="email"
              required
              placeholder="invitee@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
            />
            <select
              value={orgRole}
              onChange={(e) => setOrgRole(e.target.value)}
              className="rounded border border-gray-300 px-3 py-2 text-sm"
            >
              {ORG_ROLES_INVITABLE.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <select
              value={expiresHours}
              onChange={(e) => setExpiresHours(Number(e.target.value))}
              className="rounded border border-gray-300 px-3 py-2 text-sm"
            >
              <option value={24}>24 hours</option>
              <option value={48}>48 hours</option>
              <option value={72}>72 hours (default)</option>
              <option value={168}>7 days</option>
            </select>
            <button
              type="submit"
              disabled={creating}
              className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {creating ? "Sending…" : "Send Invite"}
            </button>
          </div>
        </form>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <div className="overflow-hidden rounded border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Email</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Role</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Status</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Expires</th>
                {isAdmin && (
                  <th className="px-4 py-3 text-right font-medium text-gray-500">Actions</th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {list.map((inv) => (
                <tr key={inv.id}>
                  <td className="px-4 py-3 text-gray-900">{inv.invited_email}</td>
                  <td className="px-4 py-3 text-gray-500">{inv.org_role ?? "—"}</td>
                  <td className="px-4 py-3">{statusBadge(inv)}</td>
                  <td className="px-4 py-3 text-gray-500">
                    {new Date(inv.expires_at).toLocaleDateString()}
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-right">
                      {inv.is_active && (
                        <button
                          onClick={() => handleRevoke(inv.id)}
                          className="text-xs text-red-600 hover:underline"
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {list.length === 0 && (
            <p className="px-4 py-6 text-center text-sm text-gray-400">No invitations.</p>
          )}
        </div>
      )}

      <style jsx>{`
        .badge-green {
          @apply inline-block rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700;
        }
        .badge-red {
          @apply inline-block rounded bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700;
        }
        .badge-gray {
          @apply inline-block rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500;
        }
        .badge-blue {
          @apply inline-block rounded bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700;
        }
      `}</style>
    </div>
  );
}
