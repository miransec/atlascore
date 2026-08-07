"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import {
  PageHeader,
  Button,
  Input,
  EmptyState,
  ErrorState,
  StatusBadge,
} from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { ApiError, knowledge, type SearchResult } from "@/lib/api";

export default function SearchPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(10);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  async function runSearch() {
    if (!user?.workspace_id || !query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await knowledge.search(user.workspace_id, {
        query: query.trim(),
        limit,
      });
      setResults(res.results);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Search failed");
      setResults(null);
    } finally {
      setLoading(false);
    }
  }

  if (!user?.workspace_id) {
    return (
      <EmptyState
        icon={Search}
        title="No active workspace"
        actionLabel="Workspaces"
        onAction={() => router.push("/dashboard/workspaces")}
      />
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Search"
        description="Hybrid lexical + vector retrieval across the active workspace."
      />

      <div className="mb-6 flex flex-col gap-3 sm:flex-row">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search workspace knowledge…"
          onKeyDown={(e) => {
            if (e.key === "Enter") void runSearch();
          }}
          className="flex-1"
        />
        <select
          className="h-9 rounded-md border border-border bg-surface px-3 text-sm"
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
        >
          {[5, 10, 20, 50].map((n) => (
            <option key={n} value={n}>
              {n} results
            </option>
          ))}
        </select>
        <Button loading={loading} onClick={() => void runSearch()}>
          Search
        </Button>
      </div>

      {error ? <ErrorState description={error} onRetry={() => void runSearch()} /> : null}

      {results && results.length === 0 ? (
        <EmptyState
          icon={Search}
          title="No results"
          description="Try a different query or ingest more documents."
        />
      ) : null}

      {results && results.length > 0 ? (
        <div className="space-y-3">
          <p className="text-xs text-muted">{results.length} results</p>
          {results.map((r) => (
            <article key={r.chunk_id} className="surface-card p-4 animate-slide-up">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">{r.document_title}</h3>
                <StatusBadge tone="neutral">{r.source_name}</StatusBadge>
                <span className="text-[11px] text-muted">chunk {r.chunk_index}</span>
              </div>
              <p className="mt-2 text-sm text-muted">
                {expanded === r.chunk_id ? r.content : `${r.content.slice(0, 220)}${r.content.length > 220 ? "…" : ""}`}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
                <span>Relevance ranked</span>
                {r.lexical_rank != null ? <span>lexical #{r.lexical_rank}</span> : null}
                {r.vector_rank != null ? <span>vector #{r.vector_rank}</span> : null}
                <button
                  type="button"
                  className="text-accent-hover hover:underline"
                  onClick={() =>
                    setExpanded((id) => (id === r.chunk_id ? null : r.chunk_id))
                  }
                >
                  {expanded === r.chunk_id ? "Collapse" : "Expand"}
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : null}

      {!results && !loading && !error ? (
        <EmptyState
          icon={Search}
          title="Search your workspace"
          description="Results combine full-text and vector relevance under access control."
        />
      ) : null}
    </div>
  );
}
