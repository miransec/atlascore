"use client";

/**
 * Knowledge administration page — /dashboard/settings/knowledge
 *
 * Phase 2A: workspace selector, sources list/create/toggle,
 * document upload/archive, ingestion job status display and retry.
 *
 * SECURITY:
 * - storage_key is never present in the API response — it cannot be displayed.
 * - Content is described only by filename and status; never as "AI learned".
 * - error_message is shown only to admins (owner / administrator).
 */

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import {
  knowledge,
  KnowledgeDocument,
  KnowledgeIngestionJob,
  KnowledgeSource,
  Workspace,
  workspaces as workspacesApi,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Status badge helpers
// ---------------------------------------------------------------------------

type JobStatus = KnowledgeIngestionJob["status"];

const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Processing",
  succeeded: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
};

const STATUS_CLASS: Record<JobStatus, string> = {
  queued: "bg-gray-100 text-gray-600",
  running: "bg-blue-100 text-blue-700",
  succeeded: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-amber-100 text-amber-700",
};

function StatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Error banner
// ---------------------------------------------------------------------------

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function KnowledgePage() {
  const { user } = useAuth();

  const canWrite = ["owner", "administrator", "workflow_builder"].includes(user?.org_role ?? "");
  const canRetry = ["owner", "administrator"].includes(user?.org_role ?? "");

  // Workspaces
  const [wsList, setWsList] = useState<Workspace[]>([]);
  const [wsError, setWsError] = useState<string | null>(null);
  const [selectedWs, setSelectedWs] = useState<Workspace | null>(null);

  // Sources
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<KnowledgeSource | null>(null);

  // Documents + jobs
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [jobs, setJobs] = useState<KnowledgeIngestionJob[]>([]);
  const [docsError, setDocsError] = useState<string | null>(null);

  // Create source form
  const [showCreateSource, setShowCreateSource] = useState(false);
  const [newSourceName, setNewSourceName] = useState("");
  const [newSourceDesc, setNewSourceDesc] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Upload
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Retry
  const [retryingJob, setRetryingJob] = useState<string | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);

  // Load workspaces on mount
  useEffect(() => {
    workspacesApi
      .list()
      .then((ws: Workspace[]) => {
        setWsList(ws);
        if (ws.length === 1) setSelectedWs(ws[0]);
      })
      .catch((e: Error) => setWsError(e.message ?? "Failed to load workspaces"));
  }, []);

  // Load sources when workspace selected
  useEffect(() => {
    if (!selectedWs) return;
    setSources([]);
    setSelectedSource(null);
    setSourcesError(null);
    knowledge
      .listSources(selectedWs.id)
      .then((s: KnowledgeSource[]) => setSources(s))
      .catch((e: Error) => setSourcesError(e.message ?? "Failed to load sources"));
  }, [selectedWs]);

  // Load documents + jobs when source selected
  useEffect(() => {
    if (!selectedWs || !selectedSource) return;
    setDocsError(null);
    Promise.all([
      knowledge.listDocuments(selectedWs.id, { source_id: selectedSource.id }),
      knowledge.listJobs(selectedWs.id),
    ])
      .then(([docs, allJobs]: [KnowledgeDocument[], KnowledgeIngestionJob[]]) => {
        setDocuments(docs);
        const docIds = new Set(docs.map((d: KnowledgeDocument) => d.id));
        setJobs(allJobs.filter((j: KnowledgeIngestionJob) => docIds.has(j.document_id)));
      })
      .catch((e: Error) => setDocsError(e.message ?? "Failed to load documents"));
  }, [selectedWs, selectedSource]);

  async function handleCreateSource(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!selectedWs) return;
    setCreating(true);
    setCreateError(null);
    try {
      const src = await knowledge.createSource(selectedWs.id, {
        source_type: "manual_upload",
        display_name: newSourceName.trim(),
        description: newSourceDesc.trim() || null,
      });
      setSources((prev: KnowledgeSource[]) => [...prev, src]);
      setNewSourceName("");
      setNewSourceDesc("");
      setShowCreateSource(false);
    } catch (err: unknown) {
      setCreateError(err instanceof Error ? err.message : "Failed to create source");
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleSource(src: KnowledgeSource) {
    if (!selectedWs) return;
    try {
      const updated = await knowledge.updateSource(selectedWs.id, src.id, {
        is_active: !src.is_active,
      });
      setSources((prev: KnowledgeSource[]) =>
        prev.map((s: KnowledgeSource) => (s.id === src.id ? updated : s)),
      );
      if (selectedSource?.id === src.id) setSelectedSource(updated);
    } catch {
      // stale state — user can refresh the page
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    if (!selectedWs || !selectedSource) return;
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const result = await knowledge.uploadDocument(selectedWs.id, selectedSource.id, file);
      setDocuments((prev: KnowledgeDocument[]) => [...prev, result.document]);
      setJobs((prev: KnowledgeIngestionJob[]) => [...prev, result.ingestion_job]);
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleArchive(doc: KnowledgeDocument) {
    if (!selectedWs) return;
    try {
      const updated = await knowledge.archiveDocument(selectedWs.id, doc.id);
      setDocuments((prev: KnowledgeDocument[]) =>
        prev.map((d: KnowledgeDocument) => (d.id === doc.id ? updated : d)),
      );
    } catch {
      // stale state
    }
  }

  async function handleRetry(job: KnowledgeIngestionJob) {
    if (!selectedWs) return;
    setRetryingJob(job.id);
    setRetryError(null);
    try {
      const updated = await knowledge.retryJob(selectedWs.id, job.id);
      setJobs((prev: KnowledgeIngestionJob[]) =>
        prev.map((j: KnowledgeIngestionJob) => (j.id === job.id ? updated : j)),
      );
    } catch (err: unknown) {
      setRetryError(err instanceof Error ? err.message : "Retry failed");
    } finally {
      setRetryingJob(null);
    }
  }

  function latestJobForDoc(docId: string): KnowledgeIngestionJob | null {
    const candidates = jobs
      .filter((j: KnowledgeIngestionJob) => j.document_id === docId)
      .sort(
        (a: KnowledgeIngestionJob, b: KnowledgeIngestionJob) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      );
    return candidates[0] ?? null;
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Knowledge</h1>
        <p className="mt-1 text-sm text-gray-500">
          Manage document sources and monitor ingestion status.
        </p>
      </div>

      {/* Workspace selector */}
      <section>
        <label className="block text-xs font-semibold uppercase tracking-widest text-gray-400 mb-2">
          Workspace
        </label>
        {wsError && <ErrorBanner message={wsError} />}
        {wsList.length === 0 && !wsError && (
          <p className="text-sm text-gray-400">Loading workspaces…</p>
        )}
        {wsList.length > 0 && (
          <select
            className="rounded border border-gray-300 px-3 py-2 text-sm"
            value={selectedWs?.id ?? ""}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => {
              const found = wsList.find((w: Workspace) => w.id === e.target.value);
              setSelectedWs(found ?? null);
            }}
          >
            <option value="">Select a workspace…</option>
            {wsList.map((ws: Workspace) => (
              <option key={ws.id} value={ws.id}>
                {ws.display_name}
              </option>
            ))}
          </select>
        )}
      </section>

      {/* Sources panel */}
      {selectedWs && (
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-medium text-gray-800">Sources</h2>
            {canWrite && (
              <button
                onClick={() => setShowCreateSource((v: boolean) => !v)}
                className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
              >
                {showCreateSource ? "Cancel" : "New Source"}
              </button>
            )}
          </div>

          {showCreateSource && (
            <form
              onSubmit={handleCreateSource}
              className="mb-4 rounded border border-gray-200 bg-gray-50 p-4 space-y-3"
            >
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Name
                </label>
                <input
                  className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm"
                  placeholder="My document collection"
                  value={newSourceName}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    setNewSourceName(e.target.value)
                  }
                  required
                  minLength={1}
                  maxLength={255}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Description (optional)
                </label>
                <textarea
                  className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm"
                  placeholder="What documents will go here?"
                  value={newSourceDesc}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                    setNewSourceDesc(e.target.value)
                  }
                  maxLength={2000}
                  rows={2}
                />
              </div>
              {createError && <ErrorBanner message={createError} />}
              <button
                type="submit"
                disabled={creating || !newSourceName.trim()}
                className="rounded bg-indigo-600 px-4 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {creating ? "Creating…" : "Create"}
              </button>
            </form>
          )}

          {sourcesError && <ErrorBanner message={sourcesError} />}

          {sources.length === 0 && !sourcesError && (
            <p className="text-sm text-gray-400">
              No sources yet. Create one to start uploading documents.
            </p>
          )}

          <ul className="space-y-2">
            {sources.map((src: KnowledgeSource) => {
              const active = selectedSource?.id === src.id;
              return (
                <li
                  key={src.id}
                  className={`flex items-center justify-between rounded border p-3 cursor-pointer transition-colors ${
                    active
                      ? "border-indigo-400 bg-indigo-50"
                      : "border-gray-200 bg-white hover:bg-gray-50"
                  }`}
                  onClick={() => setSelectedSource(active ? null : src)}
                >
                  <div>
                    <span className="text-sm font-medium text-gray-800">
                      {src.display_name}
                    </span>
                    {src.description && (
                      <p className="text-xs text-gray-500 mt-0.5">{src.description}</p>
                    )}
                    <p className="text-xs text-gray-400 mt-0.5">
                      {src.is_active ? "Active" : "Inactive"} · created {fmtDate(src.created_at)}
                    </p>
                  </div>
                  <div
                    className="flex items-center gap-2"
                    onClick={(e: React.MouseEvent) => e.stopPropagation()}
                  >
                    {!src.is_active && (
                      <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
                        Inactive
                      </span>
                    )}
                    {canWrite && (
                      <button
                        onClick={() => handleToggleSource(src)}
                        className="text-xs text-indigo-600 hover:underline"
                      >
                        {src.is_active ? "Deactivate" : "Activate"}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* Documents panel */}
      {selectedWs && selectedSource && (
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-medium text-gray-800">
              Documents in{" "}
              <span className="font-semibold">{selectedSource.display_name}</span>
            </h2>
            {canWrite && selectedSource.is_active && (
              <label className="cursor-pointer rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700">
                {uploading ? "Uploading…" : "Upload File"}
                <input
                  ref={fileInputRef}
                  type="file"
                  className="sr-only"
                  accept=".txt,.md"
                  disabled={uploading}
                  onChange={handleUpload}
                />
              </label>
            )}
          </div>

          {uploadError && <ErrorBanner message={uploadError} />}
          {retryError && <ErrorBanner message={retryError} />}
          {docsError && <ErrorBanner message={docsError} />}

          {documents.length === 0 && !docsError && (
            <p className="text-sm text-gray-400">
              No documents yet. Upload a .txt or .md file to get started.
            </p>
          )}

          <ul className="space-y-2">
            {documents.map((doc: KnowledgeDocument) => {
              const job = latestJobForDoc(doc.id);
              return (
                <li
                  key={doc.id}
                  className={`rounded border p-3 ${
                    doc.is_archived
                      ? "border-gray-100 bg-gray-50 opacity-60"
                      : "border-gray-200 bg-white"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-gray-800">
                        {doc.original_filename}
                      </p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {doc.media_type} · uploaded {fmtDate(doc.created_at)}
                        {doc.is_archived && " · archived"}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {job && <StatusBadge status={job.status} />}
                      {canWrite && !doc.is_archived && (
                        <button
                          onClick={() => handleArchive(doc)}
                          className="text-xs text-gray-400 hover:text-red-600"
                        >
                          Archive
                        </button>
                      )}
                      {canRetry && job?.status === "failed" && (
                        <button
                          disabled={retryingJob === job.id}
                          onClick={() => handleRetry(job)}
                          className="text-xs text-indigo-600 hover:underline disabled:opacity-50"
                        >
                          {retryingJob === job.id ? "Retrying…" : "Retry"}
                        </button>
                      )}
                    </div>
                  </div>
                  {job?.status === "failed" && job.error_message && canRetry && (
                    <p className="mt-1 text-xs text-red-600">{job.error_message}</p>
                  )}
                  {job && (
                    <p className="mt-1 text-xs text-gray-400">
                      Ingestion {STATUS_LABEL[job.status].toLowerCase()}
                      {job.finished_at ? ` · ${fmtDate(job.finished_at)}` : ""}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
