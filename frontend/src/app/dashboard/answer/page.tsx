"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Copy,
  FolderOpen,
  MessageSquareText,
  Upload,
} from "lucide-react";
import {
  PageHeader,
  Button,
  Textarea,
  EmptyState,
  StatusBadge,
  ErrorState,
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  knowledge,
  type AnswerCitation,
  type AnswerResponse,
} from "@/lib/api";
import { cn } from "@/lib/cn";

function bandTone(band: AnswerResponse["evidence_band"]) {
  if (band === "high") return "success" as const;
  if (band === "medium") return "accent" as const;
  if (band === "low") return "warning" as const;
  return "neutral" as const;
}

function renderAnswer(text: string) {
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    if (m) {
      return (
        <sup key={i} className="mx-0.5 font-semibold text-accent-hover">
          [{m[1]}]
        </sup>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

export default function AskAiPage() {
  const { user } = useAuth();
  const toast = useToast();
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState<"idle" | "retrieve" | "generate">("idle");
  const [result, setResult] = useState<AnswerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AnswerCitation | null>(null);

  const canAsk = Boolean(user?.workspace_id) && question.trim().length > 0 && !loading;

  async function ask() {
    if (!user?.workspace_id || !question.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setSelected(null);
    setStage("retrieve");
    const stageTimer = window.setTimeout(() => setStage("generate"), 450);
    try {
      const res = await knowledge.answer(user.workspace_id, {
        question: question.trim(),
        top_k: 10,
      });
      setResult(res);
      if (res.citations[0]) setSelected(res.citations[0]);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? "We could not complete that request. Try again."
          : "We could not complete that request. Try again.",
      );
    } finally {
      window.clearTimeout(stageTimer);
      setStage("idle");
      setLoading(false);
    }
  }

  const statusBadge = useMemo(() => {
    if (!result) return null;
    if (result.status === "answer") return <StatusBadge tone="success">Answer</StatusBadge>;
    if (result.status === "provider_failure")
      return <StatusBadge tone="danger">Provider unavailable</StatusBadge>;
    return <StatusBadge tone="warning">Abstained</StatusBadge>;
  }, [result]);

  if (!user?.workspace_id) {
    return (
      <EmptyState
        icon={MessageSquareText}
        title="Select a workspace first"
        description="Grounded answering requires an active workspace context."
        actionLabel="Open workspaces"
        onAction={() => router.push("/dashboard/workspaces")}
      />
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Ask AI"
        description="Answers are grounded only in retrieved workspace evidence. AtlasCore abstains when evidence is insufficient."
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          <div className="surface-card p-4">
            <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-muted">
              Question
            </label>
            <Textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask AtlasCore about your workspace knowledge"
              rows={5}
              maxLength={2000}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canAsk) {
                  e.preventDefault();
                  void ask();
                }
              }}
            />
            <div className="mt-3 flex items-center justify-between gap-3">
              <p className="text-xs text-muted">⌘/Ctrl + Enter to submit</p>
              <Button loading={loading} disabled={!canAsk} onClick={() => void ask()}>
                Ask AtlasCore
              </Button>
            </div>
            {loading ? (
              <p className="mt-3 text-xs text-accent-hover animate-pulse-soft">
                {stage === "retrieve" ? "Retrieving evidence…" : "Generating grounded answer…"}
              </p>
            ) : null}
          </div>

          {error ? <ErrorState description={error} onRetry={() => void ask()} /> : null}

          {result ? (
            <div className="surface-card animate-slide-up p-5">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                {statusBadge}
                <StatusBadge tone={bandTone(result.evidence_band)}>
                  Evidence {result.evidence_band}
                </StatusBadge>
                <span className="text-xs text-muted">
                  {result.provider} · {result.model}
                </span>
              </div>

              {result.status === "answer" ? (
                <>
                  <div className="prose-invert text-sm leading-relaxed text-foreground">
                    {renderAnswer(result.answer_text)}
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={async () => {
                        await navigator.clipboard.writeText(result.answer_text);
                        toast.success("Answer copied");
                      }}
                    >
                      <Copy className="h-3.5 w-3.5" />
                      Copy answer
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setResult(null);
                        setQuestion("");
                      }}
                    >
                      Ask another question
                    </Button>
                  </div>
                </>
              ) : result.status === "provider_failure" ? (
                <p className="text-sm text-muted">
                  The answer provider is temporarily unavailable. No model internals were exposed.
                  Try again shortly, or switch to the deterministic provider in server configuration.
                </p>
              ) : (
                <div>
                  <p className="text-sm text-muted">
                    {result.status === "abstain_no_evidence"
                      ? "No relevant workspace evidence was found for that question."
                      : "Evidence was too weak to ground a reliable answer."}
                  </p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button size="sm" onClick={() => router.push("/dashboard/documents")}>
                      <Upload className="h-3.5 w-3.5" />
                      Upload document
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => router.push("/dashboard/sources")}
                    >
                      <FolderOpen className="h-3.5 w-3.5" />
                      Browse sources
                    </Button>
                  </div>
                </div>
              )}

              {result.suspicious_count > 0 ? (
                <div className="mt-4 flex items-start gap-2 rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {result.suspicious_count} evidence item(s) matched prompt-injection heuristics.
                  Content was still treated as untrusted data.
                </div>
              ) : null}

              {result.limitations.length > 0 ? (
                <div className="mt-4 border-t border-border pt-4">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
                    Limitations
                  </h3>
                  <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted">
                    {result.limitations.map((l) => (
                      <li key={l}>{l}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {result.citations.length > 0 ? (
                <div className="mt-4 border-t border-border pt-4 xl:hidden">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                    Citations
                  </h3>
                  <div className="space-y-2">
                    {result.citations.map((c) => (
                      <CitationBlock
                        key={c.citation_id}
                        citation={c}
                        active={selected?.citation_id === c.citation_id}
                        onSelect={() => setSelected(c)}
                        onCopy={async () => {
                          await navigator.clipboard.writeText(
                            c.excerpt ?? `${c.document_title} [${c.label}]`,
                          );
                          toast.success("Citation copied");
                        }}
                      />
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : !loading && !error ? (
            <EmptyState
              icon={MessageSquareText}
              title="Ask AtlasCore about your workspace knowledge"
              description="Questions are answered only from retrieved documents in the active workspace."
            />
          ) : null}
        </div>

        <aside className="hidden xl:block">
          <div className="sticky top-20 surface-card max-h-[calc(100vh-7rem)] overflow-y-auto p-4 animate-slide-in-right">
            <h2 className="text-sm font-semibold">Evidence</h2>
            <p className="mt-1 text-xs text-muted">
              Citations expand with provenance from retrieval — never from model memory.
            </p>
            <div className="mt-4 space-y-2">
              {result?.citations?.length ? (
                result.citations.map((c) => (
                  <CitationBlock
                    key={c.citation_id}
                    citation={c}
                    active={selected?.citation_id === c.citation_id}
                    onSelect={() => setSelected(c)}
                    onCopy={async () => {
                      await navigator.clipboard.writeText(
                        c.excerpt ?? `${c.document_title} [${c.label}]`,
                      );
                      toast.success("Citation copied");
                    }}
                  />
                ))
              ) : (
                <p className="text-xs text-muted">Citations appear after a grounded answer.</p>
              )}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function CitationBlock({
  citation,
  active,
  onSelect,
  onCopy,
}: {
  citation: AnswerCitation;
  active?: boolean;
  onSelect: () => void;
  onCopy: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-md border p-3 text-left transition",
        active
          ? "border-accent/50 bg-accent-soft"
          : "border-border bg-surface hover:border-accent/30",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-accent-hover">[{citation.label}]</span>
        <button
          type="button"
          className="text-muted hover:text-foreground"
          onClick={(e) => {
            e.stopPropagation();
            onCopy();
          }}
          aria-label="Copy citation"
        >
          <Copy className="h-3 w-3" />
        </button>
      </div>
      <p className="mt-1 text-xs font-medium text-foreground">{citation.document_title}</p>
      <p className="text-[11px] text-muted">
        {citation.source_name} · v{citation.version_number} · chunk {citation.chunk_index}
      </p>
      {citation.excerpt ? (
        <p className="mt-2 line-clamp-4 text-xs text-muted">{citation.excerpt}</p>
      ) : null}
    </button>
  );
}
