"use client";

/**
 * Settings > Teams — create, list, manage team memberships.
 */

import { useEffect, useState, useCallback } from "react";
import { teams, Team, PaginatedTeams } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function TeamsPage() {
  const { user } = useAuth();
  const [teamList, setTeamList] = useState<Team[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<{ id: string; user_id: string; created_at: string }[]>([]);

  // Create form
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  // Add member form
  const [addUserId, setAddUserId] = useState("");

  const adminRoles = ["owner", "administrator"];
  const isAdmin = user && adminRoles.includes(user.org_role ?? "");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data: PaginatedTeams = await teams.list({ page: 1, page_size: 100 });
      setTeamList(data.items);
      setTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function loadMembers(teamId: string) {
    try {
      const data = await teams.listMembers(teamId);
      setMembers(data);
    } catch {
      setMembers([]);
    }
  }

  async function handleCreate(ev: React.FormEvent) {
    ev.preventDefault();
    setActionError(null);
    setCreating(true);
    try {
      await teams.create({ name: newName, description: newDesc || null });
      setNewName("");
      setNewDesc("");
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this team and all its memberships?")) return;
    setActionError(null);
    try {
      await teams.delete(id);
      if (selectedTeam?.id === id) setSelectedTeam(null);
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  async function selectTeam(team: Team) {
    setSelectedTeam(team);
    await loadMembers(team.id);
  }

  async function handleAddMember(ev: React.FormEvent) {
    ev.preventDefault();
    if (!selectedTeam) return;
    setActionError(null);
    try {
      await teams.addMember(selectedTeam.id, addUserId.trim());
      setAddUserId("");
      await loadMembers(selectedTeam.id);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  async function handleRemoveMember(userId: string) {
    if (!selectedTeam) return;
    setActionError(null);
    try {
      await teams.removeMember(selectedTeam.id, userId);
      await loadMembers(selectedTeam.id);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Unknown error.");
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold text-gray-900">Teams</h1>

      {error && (
        <p className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}
      {actionError && (
        <p className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">{actionError}</p>
      )}

      <div className="flex gap-6">
        {/* Team list */}
        <div className="flex-1">
          {isAdmin && (
            <form onSubmit={handleCreate} className="mb-4 flex gap-2">
              <input
                required
                placeholder="Team name"
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
                    <th className="px-4 py-3 text-left font-medium text-gray-500">Description</th>
                    {isAdmin && (
                      <th className="px-4 py-3 text-right font-medium text-gray-500">Actions</th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {teamList.map((t) => (
                    <tr
                      key={t.id}
                      className={`cursor-pointer ${selectedTeam?.id === t.id ? "bg-indigo-50" : "hover:bg-gray-50"}`}
                      onClick={() => selectTeam(t)}
                    >
                      <td className="px-4 py-3 font-medium text-gray-900">{t.name}</td>
                      <td className="px-4 py-3 text-gray-500">{t.description ?? "—"}</td>
                      {isAdmin && (
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDelete(t.id);
                            }}
                            className="text-xs text-red-600 hover:underline"
                          >
                            Delete
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              {teamList.length === 0 && (
                <p className="px-4 py-6 text-center text-sm text-gray-400">No teams yet.</p>
              )}
            </div>
          )}
        </div>

        {/* Team members panel */}
        {selectedTeam && (
          <div className="w-80 shrink-0 rounded border border-gray-200 p-4">
            <h2 className="mb-3 text-sm font-semibold text-gray-700">
              Members — {selectedTeam.name}
            </h2>

            {isAdmin && (
              <form onSubmit={handleAddMember} className="mb-4 flex gap-2">
                <input
                  required
                  placeholder="User ID (UUID)"
                  value={addUserId}
                  onChange={(e) => setAddUserId(e.target.value)}
                  className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs"
                />
                <button
                  type="submit"
                  className="rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-700"
                >
                  Add
                </button>
              </form>
            )}

            <ul className="divide-y divide-gray-100 text-sm">
              {members.map((m) => (
                <li key={m.id} className="flex items-center justify-between py-2">
                  <span className="font-mono text-xs text-gray-500">{m.user_id.slice(0, 8)}…</span>
                  {isAdmin && (
                    <button
                      onClick={() => handleRemoveMember(m.user_id)}
                      className="text-xs text-red-500 hover:underline"
                    >
                      Remove
                    </button>
                  )}
                </li>
              ))}
              {members.length === 0 && (
                <li className="py-4 text-center text-xs text-gray-400">No members.</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
