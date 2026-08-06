"use client";

/**
 * Knowledge Search page — /dashboard/search
 *
 * Phase 2B: hybrid lexical + vector search over ingested knowledge.
 *
 * This is a SEARCH interface, not a chat interface.
 * - No AI-generated answers.
 * - No fake confidence percentages.
 * - No chat bubbles or message threads.
 * - For no results: plain "No matching knowledge found."
 * - Results are ranked evidence chunks with provenance.
 *
 * SECURITY:
 * - storage_key is never present in API response — it cannot be displayed.
 * - Chunk content is rendered as plain text (not innerHTML) to prevent XSS.
 * - The workspace_id used in the API call comes from the authenticated JWT,
 *   not from client state that could be tampered with.
 */

import { FormEvent, useCallback, useRef, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { knowledge, SearchResponse, SearchResult } from "@/lib/api";
import { KnowledgeShell } from "@/components/KnowledgeShell";

// ---------------------------------------------------------------------------
// Score badge — shows which retrieval channels contributed to this result
// ---------------------------------------------------------------------------

function ChannelBadge({
  label,
  active,
}: {
  label: string;
  active: boolean;
}) {
  if (!active) return null;
  return (
    <span className="inline-block rounded bg-indigo-50 px-1.5 py-0.5 text-xs font-medium text-indigo-600">
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Single result card
// ---------------------------------------------------------------------------

function ResultCard({
  result,
  index,
}: {
  result: SearchResult;
  index: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const PREVIEW_CHARS = 300;
  const isLong = result.content.length > PREVIEW_CHARS;
  const displayContent = expanded
    ? result.content
    : result.content.slice(0, PREVIEW_CHARS) + (isLong ? "…" : "");

  return (
    <article className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      {/* Header row: rank + provenance */}
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="shrink-0 w-6 h-6 rounded-full bg-gray-100 text-gray-500 text-xs font-semibold flex items-center justify-center">
              {index + 1}
            </span>
            <span className="font-medium text-gray-900 text-sm truncate">
              {result.document_title}
            </span>
            <span className="text-gray-400 text-xs">·</span>
            <span className="text-gray-500 text-xs">{result.source_name}</span>
            <span className="text-gray-400 text-xs">
              v{result.version_number} · chunk {result.chunk_index}
            </span>
          </div>
        </div>
        {/* Channel badges */}
        <div className="flex gap-1 shrink-0">
          <ChannelBadge label="lexical" active={result.lexical_rank !== null} />
          <ChannelBadge label="vector" active={result.vector_rank !== null} />
        </div>
      </div>

      {/* Chunk content — rendered as plain text (XSS-safe) */}
      <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap break-words">
        {displayContent}
      </p>

      {/* Expand toggle */}
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="mt-2 text-xs text-indigo-600 hover:underline"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Main search page
// ---------------------------------------------------------------------------

export default function SearchPage() {
  const { user } = useAuth();
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(10);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const workspaceId = user?.workspace_id ?? null;

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const trimmed = query.trim();
      if (!trimmed || !workspaceId) return;

      setLoading(true);
      setError(null);
      setSubmitted(true);

      try {
        const data = await knowledge.search(workspaceId, {
          query: trimmed,
          limit,
        });
        setResponse(data);
      } catch (err) {
        setError("An unexpected error occurred. Please try again.");
        setResponse(null);
      } finally {
        setLoading(false);
      }
    },
    [query, limit, workspaceId],
  );

  const handleClear = () => {
    setQuery("");
    setResponse(null);
    setSubmitted(false);
    setError(null);
    inputRef.current?.focus();
  };

  if (!workspaceId) {
    return (
      <KnowledgeShell heading="Knowledge Search">
        <div className="rounded border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          No workspace is selected. Switch to a workspace to search its knowledge.
        </div>
      </KnowledgeShell>
    );
  }

  return (
    <KnowledgeShell
      heading="Knowledge Search"
      description="Search across ingested documents using full-text and semantic matching."
    >
    <div className="max-w-3xl">

      {/* Search form */}
      <form onSubmit={handleSubmit} className="mb-6">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search knowledge…"
            maxLength={2000}
            className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            aria-label="Search query"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Searching…" : "Search"}
          </button>
          {submitted && (
            <button
              type="button"
              onClick={handleClear}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
            >
              Clear
            </button>
          )}
        </div>

        {/* Result count control */}
        <div className="mt-2 flex items-center gap-2">
          <label
            htmlFor="limit"
            className="text-xs text-gray-500"
          >
            Show up to
          </label>
          <select
            id="limit"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="rounded border border-gray-200 px-2 py-0.5 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-indigo-400"
          >
            <option value={5}>5</option>
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
          </select>
          <span className="text-xs text-gray-500">results</span>
        </div>
      </form>

      {/* Error state */}
      {error && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Results */}
      {response !== null && (
        <>
          {response.results.length === 0 ? (
            <p className="text-sm text-gray-500">
              No matching knowledge found.
            </p>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-gray-400">
                {response.total} result{response.total !== 1 ? "s" : ""} ranked
                by relevance
              </p>
              {response.results.map((r, i) => (
                <ResultCard key={r.chunk_id} result={r} index={i} />
              ))}
            </div>
          )}
        </>
      )}

      {/* Initial state before any search */}
      {!submitted && !error && (
        <p className="text-sm text-gray-400">
          Enter a search query to find relevant knowledge chunks.
        </p>
      )}
    </div>
    </KnowledgeShell>
  );
}
