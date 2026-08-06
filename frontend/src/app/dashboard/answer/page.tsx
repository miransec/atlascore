"use client";

/**
 * Grounded Q&A page — /dashboard/answer
 *
 * Phase 2C: Ask a question, get an answer grounded entirely in the
 * workspace's knowledge base.
 *
 * Key properties:
 * - Answers are grounded in retrieved evidence only. The LLM is explicitly
 *   told NOT to use general knowledge.
 * - If evidence is insufficient, the system abstains — it does not guess.
 * - Each claim is traceable to a specific document chunk (citation).
 * - Evidence band (high/medium/low) is shown as a confidence indicator.
 *   This reflects retrieval signal quality, NOT model self-assessment.
 * - Provider failures return a safe generic message; no internals exposed.
 *
 * SECURITY:
 * - storage_key is never present in API response — it cannot be displayed.
 * - Embedding vectors are never returned — not in scope for this view.
 * - Provenance (source_name, document_title) comes from server-controlled
 *   EvidenceItems, never from the LLM output.
 * - Evidence content is rendered as plain text (not innerHTML) to prevent XSS.
 * - workspace_id used in the API call comes from the authenticated JWT.
 */

import { FormEvent, useCallback, useRef, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { knowledge, AnswerResponse, AnswerCitation } from "@/lib/api";
import { KnowledgeShell } from "@/components/KnowledgeShell";

// ---------------------------------------------------------------------------
// Evidence band badge
// ---------------------------------------------------------------------------

function EvidenceBandBadge({ band }: { band: AnswerResponse["evidence_band"] }) {
  const config: Record<string, { label: string; className: string }> = {
    high: {
      label: "High evidence quality",
      className: "bg-green-50 text-green-700 ring-green-200",
    },
    medium: {
      label: "Medium evidence quality",
      className: "bg-yellow-50 text-yellow-700 ring-yellow-200",
    },
    low: {
      label: "Low evidence quality",
      className: "bg-orange-50 text-orange-700 ring-orange-200",
    },
    none: {
      label: "No evidence",
      className: "bg-gray-50 text-gray-500 ring-gray-200",
    },
  };
  const c = config[band] ?? config.none;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${c.className}`}
      title="Evidence quality is derived from retrieval signals, not AI self-assessment."
    >
      {c.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Citation card
// ---------------------------------------------------------------------------

function CitationCard({ citation }: { citation: AnswerCitation }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm">
      <div className="flex items-start gap-2">
        <span className="shrink-0 w-5 h-5 rounded-full bg-indigo-100 text-indigo-700 text-xs font-bold flex items-center justify-center mt-0.5">
          {citation.label}
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-gray-900 truncate">{citation.document_title}</p>
          <p className="text-gray-500 text-xs">
            {citation.source_name} · v{citation.version_number} · chunk {citation.chunk_index}
          </p>
          {citation.excerpt && (
            <div className="mt-1.5">
              {expanded ? (
                <p className="text-gray-700 whitespace-pre-wrap break-words">
                  {citation.excerpt}
                </p>
              ) : (
                <p className="text-gray-600 line-clamp-2">{citation.excerpt}</p>
              )}
              <button
                onClick={() => setExpanded((v) => !v)}
                className="mt-1 text-xs text-indigo-600 hover:text-indigo-800"
              >
                {expanded ? "Show less" : "Show excerpt"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Answer panel
// ---------------------------------------------------------------------------

function AnswerPanel({ response }: { response: AnswerResponse }) {
  const isAbstention =
    response.status === "abstain_no_evidence" ||
    response.status === "abstain_weak_evidence";
  const isFailure = response.status === "provider_failure";

  // Render answer text with citation markers [1] highlighted.
  // The text is split on citation patterns; they're rendered as superscripts.
  function renderAnswerText(text: string) {
    const parts = text.split(/(\[\d+\])/g);
    return parts.map((part, i) => {
      const match = part.match(/^\[(\d+)\]$/);
      if (match) {
        const num = parseInt(match[1], 10);
        const citation = response.citations.find((c) => c.label === num);
        return (
          <sup
            key={i}
            title={citation ? `${citation.document_title} (${citation.source_name})` : ""}
            className="ml-0.5 cursor-default rounded bg-indigo-100 px-1 py-0.5 text-indigo-700 text-xs font-medium"
          >
            {num}
          </sup>
        );
      }
      return <span key={i}>{part}</span>;
    });
  }

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* Header bar */}
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-gray-100 bg-gray-50">
        <span className="text-sm font-medium text-gray-700">Answer</span>
        <div className="flex items-center gap-2">
          {response.evidence_band && response.status !== "provider_failure" && (
            <EvidenceBandBadge band={response.evidence_band} />
          )}
          {response.suspicious_count > 0 && (
            <span
              className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200"
              title={`${response.suspicious_count} evidence item(s) contained suspicious patterns and were flagged.`}
            >
              {response.suspicious_count} flagged
            </span>
          )}
        </div>
      </div>

      {/* Answer body */}
      <div className="px-5 py-4">
        {isAbstention || isFailure ? (
          <p className="text-gray-600">{response.answer_text}</p>
        ) : (
          <p className="text-gray-900 leading-relaxed whitespace-pre-wrap">
            {renderAnswerText(response.answer_text)}
          </p>
        )}
      </div>

      {/* Limitations */}
      {response.limitations.length > 0 && (
        <div className="px-5 pb-3">
          {response.limitations.map((lim, i) => (
            <p key={i} className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1 mt-1">
              {lim}
            </p>
          ))}
        </div>
      )}

      {/* Citations */}
      {response.citations.length > 0 && (
        <div className="px-5 pb-5">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2 mt-2">
            Sources
          </p>
          <div className="space-y-2">
            {response.citations.map((c) => (
              <CitationCard key={c.citation_id} citation={c} />
            ))}
          </div>
        </div>
      )}

      {/* Observability footer */}
      {response.provider && (
        <div className="px-5 py-2 border-t border-gray-100 text-xs text-gray-400">
          Provider: {response.provider}
          {response.model ? ` · Model: ${response.model}` : ""}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type PageState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "result"; response: AnswerResponse }
  | { kind: "error"; message: string };

export default function AnswerPage() {
  const { user } = useAuth();
  const workspaceId = user?.workspace_id ?? null;
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(10);
  const [state, setState] = useState<PageState>({ kind: "idle" });
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const q = question.trim();
      if (!q || !workspaceId) return;

      setState({ kind: "loading" });
      try {
        const response = await knowledge.answer(workspaceId, {
          question: q,
          top_k: topK,
        });
        setState({ kind: "result", response });
      } catch (err: unknown) {
        const msg =
          err instanceof Error ? err.message : "An unexpected error occurred.";
        setState({ kind: "error", message: msg });
      }
    },
    [question, topK, workspaceId],
  );

  const handleNewQuestion = useCallback(() => {
    setState({ kind: "idle" });
    setTimeout(() => inputRef.current?.focus(), 50);
  }, []);

  return (
    <KnowledgeShell
      heading="Ask a question"
      description="Answers are grounded entirely in your workspace knowledge. If the evidence is insufficient, the system says so — it never guesses."
    >
    <div className="max-w-3xl">
      {/* Question form */}
      {(state.kind === "idle" || state.kind === "error") && (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="question" className="sr-only">
              Question
            </label>
            <textarea
              id="question"
              ref={inputRef}
              rows={4}
              className="block w-full rounded-lg border border-gray-300 px-3.5 py-2.5 text-gray-900 shadow-sm placeholder:text-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 sm:text-sm resize-none"
              placeholder="e.g. What is the refund policy for enterprise customers?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={2000}
              autoFocus
            />
            <p className="mt-1 text-right text-xs text-gray-400">
              {question.length}/2000
            </p>
          </div>

          <div className="flex items-center gap-4">
            <label
              htmlFor="top_k"
              className="text-sm text-gray-600 whitespace-nowrap"
            >
              Evidence candidates:
            </label>
            <select
              id="top_k"
              className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-700 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
            >
              {[5, 10, 20, 30, 50].map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </div>

          {state.kind === "error" && (
            <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
              {state.message}
            </div>
          )}

          <button
            type="submit"
            disabled={!question.trim()}
            className="inline-flex items-center rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:ring-offset-2 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Get answer
          </button>
        </form>
      )}

      {/* Loading state */}
      {state.kind === "loading" && (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <div className="h-8 w-8 rounded-full border-4 border-indigo-200 border-t-indigo-600 animate-spin" />
          <p className="text-sm text-gray-500">Searching knowledge base…</p>
        </div>
      )}

      {/* Answer */}
      {state.kind === "result" && (
        <div className="space-y-4">
          {/* Echo the question */}
          <div className="rounded-lg bg-indigo-50 px-4 py-3">
            <p className="text-sm font-medium text-indigo-900 italic">&ldquo;{question}&rdquo;</p>
          </div>

          <AnswerPanel response={state.response} />

          <div className="flex gap-3">
            <button
              onClick={handleNewQuestion}
              className="inline-flex items-center rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
            >
              Ask another question
            </button>
          </div>
        </div>
      )}
    </div>
    </KnowledgeShell>
  );
}
