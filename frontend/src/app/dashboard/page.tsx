"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Boxes,
  FileText,
  FolderOpen,
  MessageSquareText,
  Plus,
  Upload,
  UserPlus,
} from "lucide-react";
import { PageHeader, StatCard, Button, EmptyState, LoadingSkeleton, StatusBadge } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { getHealth, knowledge, workspaces as workspacesApi, type Workspace } from "@/lib/api";

export default function DashboardPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [ws, setWs] = useState<Workspace | null>(null);
  const [sourceCount, setSourceCount] = useState(0);
  const [docCount, setDocCount] = useState(0);
  const [jobStats, setJobStats] = useState({ running: 0, failed: 0, succeeded: 0 });
  const [provider, setProvider] = useState("—");

  const load = useCallback(async () => {
    if (!user?.workspace_id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [list, sources, docs, jobs, health] = await Promise.all([
        workspacesApi.list(),
        knowledge.listSources(user.workspace_id),
        knowledge.listDocuments(user.workspace_id),
        knowledge.listJobs(user.workspace_id),
        getHealth().catch(() => null),
      ]);
      setWs(list.find((w) => w.id === user.workspace_id) ?? null);
      setSourceCount(sources.length);
      setDocCount(docs.length);
      setJobStats({
        running: jobs.filter((j) => j.status === "running" || j.status === "queued").length,
        failed: jobs.filter((j) => j.status === "failed").length,
        succeeded: jobs.filter((j) => j.status === "succeeded").length,
      });
      if (health) {
        const demo = health.demo_mode === true || health.demo_mode === "true";
        setProvider(demo ? `${health.answer_provider} (demo)` : health.answer_provider);
      }
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!user) return null;

  return (
    <div className="animate-fade-in">
      <PageHeader
        title={`Welcome, ${user.full_name.split(" ")[0]}`}
        description={`Signed in to ${user.organisation_slug}. Grounded answers stay within your active workspace.`}
        actions={
          <Button onClick={() => router.push("/dashboard/answer")}>
            <MessageSquareText className="h-4 w-4" />
            Ask AtlasCore
          </Button>
        }
      />

      {!user.workspace_id ? (
        <EmptyState
          icon={Boxes}
          title="No active workspace"
          description="Create or switch into a workspace to unlock knowledge, search, and grounded answering."
          actionLabel="Manage workspaces"
          onAction={() => router.push("/dashboard/workspaces")}
        />
      ) : loading ? (
        <LoadingSkeleton lines={6} />
      ) : (
        <>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Active workspace"
              value={ws?.display_name ?? "—"}
              hint={ws?.slug}
              icon={Boxes}
            />
            <StatCard label="Sources" value={sourceCount} icon={FolderOpen} />
            <StatCard label="Documents" value={docCount} icon={FileText} />
            <StatCard
              label="Ingestion"
              value={`${jobStats.running} active`}
              hint={`${jobStats.succeeded} succeeded · ${jobStats.failed} failed`}
              icon={Upload}
            />
          </div>

          <div className="mb-6 grid gap-4 lg:grid-cols-3">
            <div className="surface-card p-5 lg:col-span-2">
              <h2 className="text-sm font-semibold">Quick actions</h2>
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                {[
                  { label: "Upload document", href: "/dashboard/documents", icon: Upload },
                  { label: "Add source", href: "/dashboard/sources", icon: FolderOpen },
                  { label: "Ask AtlasCore", href: "/dashboard/answer", icon: MessageSquareText },
                  { label: "Invite member", href: "/dashboard/members", icon: UserPlus },
                  { label: "Create workspace", href: "/dashboard/workspaces", icon: Plus },
                ].map((a) => (
                  <button
                    key={a.href + a.label}
                    type="button"
                    onClick={() => router.push(a.href)}
                    className="flex items-center gap-3 rounded-md border border-border bg-surface px-3 py-3 text-left text-sm transition hover:-translate-y-0.5 hover:border-accent/40 hover:bg-surface-raised"
                  >
                    <a.icon className="h-4 w-4 text-accent-hover" />
                    {a.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="surface-card space-y-4 p-5">
              <h2 className="text-sm font-semibold">Provider status</h2>
              <StatusBadge tone="success" pulse>
                {provider}
              </StatusBadge>
              <p className="text-xs text-muted">
                Answers are evidence-gated. The model is never called with insufficient
                workspace evidence.
              </p>
              <h2 className="pt-2 text-sm font-semibold">Security summary</h2>
              <ul className="space-y-2 text-xs text-muted">
                <li>Organisation: {user.organisation_slug}</li>
                <li>Role: {user.org_role ?? "member"}</li>
                <li>Audit event APIs: not exposed in this release</li>
              </ul>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
