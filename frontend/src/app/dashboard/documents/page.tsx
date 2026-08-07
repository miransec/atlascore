"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { FileText, Upload } from "lucide-react";
import {
  PageHeader,
  Button,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
  DataTable,
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  knowledge,
  type KnowledgeDocument,
  type KnowledgeIngestionJob,
  type KnowledgeSource,
} from "@/lib/api";
import { cn } from "@/lib/cn";

export default function DocumentsPage() {
  const { user } = useAuth();
  const toast = useToast();
  const router = useRouter();
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [jobs, setJobs] = useState<KnowledgeIngestionJob[]>([]);
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadSourceId, setUploadSourceId] = useState("");

  const load = useCallback(async () => {
    if (!user?.workspace_id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [s, d, j] = await Promise.all([
        knowledge.listSources(user.workspace_id),
        knowledge.listDocuments(user.workspace_id, {
          source_id: sourceFilter || undefined,
          include_archived: true,
        }),
        knowledge.listJobs(user.workspace_id),
      ]);
      setSources(s);
      setDocs(d);
      setJobs(j);
      if (!uploadSourceId && s[0]) setUploadSourceId(s[0].id);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  }, [user, sourceFilter, uploadSourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const jobByDoc = useMemo(() => {
    const map = new Map<string, KnowledgeIngestionJob>();
    for (const j of jobs) {
      const prev = map.get(j.document_id);
      if (!prev || new Date(j.created_at) > new Date(prev.created_at)) {
        map.set(j.document_id, j);
      }
    }
    return map;
  }, [jobs]);

  async function uploadFiles(files: FileList | File[]) {
    if (!user?.workspace_id || !uploadSourceId) {
      toast.error("Choose a source before uploading");
      return;
    }
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await knowledge.uploadDocument(user.workspace_id, uploadSourceId, file);
      }
      toast.success("Upload queued", "Ingestion jobs are processing.");
      await load();
    } catch (err) {
      toast.error("Upload failed", err instanceof ApiError ? err.detail : "Error");
    } finally {
      setUploading(false);
    }
  }

  if (!user?.workspace_id) {
    return (
      <EmptyState
        icon={FileText}
        title="No active workspace"
        actionLabel="Workspaces"
        onAction={() => router.push("/dashboard/workspaces")}
      />
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Documents"
        description="Upload files, track ingestion, and archive outdated material."
      />

      <div
        className={cn(
          "mb-6 rounded-lg border border-dashed p-8 text-center transition",
          dragOver ? "border-accent bg-accent-soft" : "border-border bg-surface/40",
        )}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files.length) void uploadFiles(e.dataTransfer.files);
        }}
      >
        <Upload className="mx-auto mb-3 h-5 w-5 text-accent-hover" />
        <p className="text-sm font-medium">Drag & drop files here</p>
        <p className="mt-1 text-xs text-muted">Assign uploads to a source below</p>
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          <select
            className="h-9 rounded-md border border-border bg-surface px-3 text-sm"
            value={uploadSourceId}
            onChange={(e) => setUploadSourceId(e.target.value)}
          >
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name}
              </option>
            ))}
          </select>
          <label className="inline-flex cursor-pointer">
            <input
              type="file"
              className="hidden"
              multiple
              disabled={uploading || !uploadSourceId}
              onChange={(e) => {
                if (e.target.files?.length) void uploadFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <span className="inline-flex h-9 items-center rounded-md bg-accent px-3.5 text-sm font-medium text-white hover:bg-accent-hover">
              {uploading ? "Uploading…" : "Browse files"}
            </span>
          </label>
        </div>
      </div>

      <div className="mb-4 flex items-center gap-2">
        <select
          className="h-9 rounded-md border border-border bg-surface px-3 text-sm"
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
        >
          <option value="">All sources</option>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.display_name}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <LoadingSkeleton lines={5} />
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : docs.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No documents"
          description="Upload a file to start ingestion."
        />
      ) : (
        <DataTable
          rows={docs}
          columns={[
            {
              key: "name",
              header: "Document",
              cell: (d) => (
                <div>
                  <p className="font-medium">{d.original_filename}</p>
                  <p className="text-xs text-muted">{d.media_type}</p>
                </div>
              ),
            },
            {
              key: "source",
              header: "Source",
              cell: (d) =>
                sources.find((s) => s.id === d.source_id)?.display_name ?? d.source_id.slice(0, 8),
            },
            {
              key: "status",
              header: "Ingestion",
              cell: (d) => {
                const job = jobByDoc.get(d.id);
                const status = d.is_archived ? "archived" : job?.status ?? "unknown";
                const tone =
                  status === "succeeded"
                    ? "success"
                    : status === "failed"
                      ? "danger"
                      : status === "running" || status === "queued"
                        ? "accent"
                        : "neutral";
                return (
                  <StatusBadge tone={tone} pulse={status === "running" || status === "queued"}>
                    {status}
                  </StatusBadge>
                );
              },
            },
            {
              key: "uploaded",
              header: "Uploaded",
              cell: (d) => (
                <span className="text-xs text-muted">
                  {new Date(d.created_at).toLocaleString()}
                </span>
              ),
            },
            {
              key: "actions",
              header: "",
              cell: (d) => {
                const job = jobByDoc.get(d.id);
                return (
                  <div className="flex gap-1">
                    {!d.is_archived ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={async () => {
                          try {
                            await knowledge.archiveDocument(user.workspace_id!, d.id);
                            toast.success("Document archived");
                            await load();
                          } catch (err) {
                            toast.error(
                              "Archive failed",
                              err instanceof ApiError ? err.detail : "Error",
                            );
                          }
                        }}
                      >
                        Archive
                      </Button>
                    ) : null}
                    {job?.status === "failed" ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={async () => {
                          try {
                            await knowledge.retryJob(user.workspace_id!, job.id);
                            toast.success("Retry queued");
                            await load();
                          } catch (err) {
                            toast.error(
                              "Retry failed",
                              err instanceof ApiError ? err.detail : "Error",
                            );
                          }
                        }}
                      >
                        Retry
                      </Button>
                    ) : null}
                  </div>
                );
              },
            },
          ]}
        />
      )}
    </div>
  );
}
