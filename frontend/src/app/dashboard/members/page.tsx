"use client";

import { useCallback, useEffect, useState } from "react";
import { Users } from "lucide-react";
import {
  PageHeader,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  DataTable,
  StatusBadge,
  Button,
  Dialog,
  Input,
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  invitations,
  organisations,
  workspaces as workspacesApi,
  type OrgMember,
  type WorkspaceMember,
} from "@/lib/api";

export default function MembersPage() {
  const { user } = useAuth();
  const toast = useToast();
  const [orgMembers, setOrgMembers] = useState<OrgMember[]>([]);
  const [wsMembers, setWsMembers] = useState<WorkspaceMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [rawToken, setRawToken] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const org = await organisations.listMembers();
      setOrgMembers(org);
      if (user?.workspace_id) {
        try {
          setWsMembers(await workspacesApi.listMembers(user.workspace_id));
        } catch {
          setWsMembers([]);
        }
      } else {
        setWsMembers([]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to load members");
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void load();
  }, [load]);

  const wsRoleByUser = new Map(wsMembers.map((m) => [m.user_id, m.workspace_role]));

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Members"
        description="Organisation membership and workspace roles for the active workspace."
        actions={
          <Button onClick={() => setInviteOpen(true)}>Invite member</Button>
        }
      />

      {loading ? (
        <LoadingSkeleton lines={5} />
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : orgMembers.length === 0 ? (
        <EmptyState icon={Users} title="No members" />
      ) : (
        <DataTable
          rows={orgMembers}
          columns={[
            {
              key: "name",
              header: "Member",
              cell: (m) => (
                <div>
                  <p className="font-medium">{m.full_name}</p>
                  <p className="text-xs text-muted">{m.email}</p>
                </div>
              ),
            },
            {
              key: "org",
              header: "Org role",
              cell: (m) => (
                <StatusBadge tone="neutral">{m.org_role ?? "member"}</StatusBadge>
              ),
            },
            {
              key: "ws",
              header: "Workspace role",
              cell: (m) => wsRoleByUser.get(m.user_id) ?? "—",
            },
            {
              key: "status",
              header: "Status",
              cell: () => <StatusBadge tone="success">Active</StatusBadge>,
            },
          ]}
        />
      )}

      <Dialog
        open={inviteOpen}
        onClose={() => {
          setInviteOpen(false);
          setRawToken(null);
          setEmail("");
        }}
        title="Invite member"
        description="Creates an invitation. The raw token is shown once."
      >
        {rawToken ? (
          <div className="space-y-3">
            <p className="text-sm text-muted">Copy this token now — it will not be shown again.</p>
            <code className="block break-all rounded-md border border-border bg-surface p-3 text-xs">
              {rawToken}
            </code>
            <Button
              onClick={async () => {
                await navigator.clipboard.writeText(rawToken);
                toast.success("Token copied");
              }}
            >
              Copy token
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <Input
              type="email"
              placeholder="colleague@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setInviteOpen(false)}>
                Cancel
              </Button>
              <Button
                loading={inviting}
                onClick={async () => {
                  setInviting(true);
                  try {
                    const res = await invitations.create({
                      invited_email: email.trim(),
                      org_role: "viewer",
                      workspace_id: user?.workspace_id ?? null,
                      workspace_role: user?.workspace_id ? "viewer" : null,
                    });
                    setRawToken(res.raw_token);
                    toast.success("Invitation created");
                    await load();
                  } catch (err) {
                    toast.error(
                      "Invite failed",
                      err instanceof ApiError ? err.detail : "Error",
                    );
                  } finally {
                    setInviting(false);
                  }
                }}
              >
                Create invitation
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
