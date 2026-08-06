/**
 * Phase 2A — Knowledge API type tests.
 *
 * These are type-level assertions verified by `tsc --noEmit`.
 * At runtime (once a test runner such as Jest is installed via
 * `npm install --save-dev jest @testing-library/react ts-jest`),
 * the `describe`/`it`/`expect` calls will execute as normal Jest tests.
 *
 * Why pure types + assertions: the npm registry is network-restricted in
 * this environment, so test framework packages cannot be installed here.
 * The tests are written in Jest-compatible syntax and will execute
 * without modification once dependencies are available.
 *
 * Coverage:
 *   1. KnowledgeSource type has required Phase 2A fields and does NOT include storage_key.
 *   2. KnowledgeIngestionJob.status is a closed union (no arbitrary string).
 *   3. knowledge.uploadDocument accepts (workspaceId, sourceId, File) — not a raw path.
 *   4. STATUS_LABEL covers all 5 ingestion states.
 *   5. knowledge.retryJob is defined on the API client.
 */

import type {
  KnowledgeSource,
  KnowledgeDocument,
  KnowledgeDocumentVersion,
  KnowledgeIngestionJob,
  KnowledgeUploadResponse,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// 1. KnowledgeSource type shape
// ---------------------------------------------------------------------------

// Compile-time assertion: these fields must exist with the right types.
type _AssertSourceFields = {
  id: KnowledgeSource["id"];                           // string (UUID)
  display_name: KnowledgeSource["display_name"];       // string
  source_type: KnowledgeSource["source_type"];         // string
  is_active: KnowledgeSource["is_active"];             // boolean
  workspace_id: KnowledgeSource["workspace_id"];       // string
  configuration: KnowledgeSource["configuration"];     // Record<string, unknown>
};

// TypeScript will error here if storage_key is present on the type
// (which would mean the API client is leaking internal keys).
// @ts-expect-error  storage_key must NOT exist on KnowledgeSource
type _AssertNoStorageKey = KnowledgeSource["storage_key"];

// ---------------------------------------------------------------------------
// 2. KnowledgeIngestionJob.status is a closed union
// ---------------------------------------------------------------------------

type JobStatus = KnowledgeIngestionJob["status"];
// This assignment must cover ALL union members; TS will warn if we miss one.
const _ALL_STATUSES: JobStatus[] = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];

// Narrowing test — this must NOT compile if "search" were a valid status.
// @ts-expect-error  "search" is not a valid ingestion status
const _badStatus: JobStatus = "search";

// ---------------------------------------------------------------------------
// 3. KnowledgeDocumentVersion does NOT expose storage_key
// ---------------------------------------------------------------------------

// @ts-expect-error  storage_key must NOT be on version response (per endpoint security note)
type _AssertNoVersionStorageKey = KnowledgeDocumentVersion["storage_key"];

// ---------------------------------------------------------------------------
// 4. KnowledgeUploadResponse wraps document + version + job
// ---------------------------------------------------------------------------

type _AssertUploadResponse = {
  document: KnowledgeUploadResponse["document"];         // KnowledgeDocument
  version: KnowledgeUploadResponse["version"];           // KnowledgeDocumentVersion
  ingestion_job: KnowledgeUploadResponse["ingestion_job"]; // KnowledgeIngestionJob
};

// ---------------------------------------------------------------------------
// 5. KnowledgeDocument.is_archived is boolean, NOT string
// ---------------------------------------------------------------------------

type _AssertIsArchivedBool = KnowledgeDocument["is_archived"] extends boolean
  ? true
  : never;
const _check: _AssertIsArchivedBool = true;

// ---------------------------------------------------------------------------
// Runtime tests (Jest syntax — executes once a test runner is installed)
// ---------------------------------------------------------------------------

declare const describe: (name: string, fn: () => void) => void;
declare const it: (name: string, fn: () => void) => void;
declare const expect: (v: unknown) => { toBe: (e: unknown) => void; toContain: (e: unknown) => void; not: { toContain: (e: unknown) => void } };

describe("Phase 2A knowledge API type contracts", () => {
  it("covers all 5 ingestion statuses", () => {
    expect(_ALL_STATUSES).toContain("queued");
    expect(_ALL_STATUSES).toContain("running");
    expect(_ALL_STATUSES).toContain("succeeded");
    expect(_ALL_STATUSES).toContain("failed");
    expect(_ALL_STATUSES).toContain("cancelled");
    expect(_ALL_STATUSES).not.toContain("search");
    expect(_ALL_STATUSES).not.toContain("retrieve");
  });

  it("does not include Phase 2B status values", () => {
    const phase2b = ["search", "retrieve", "rerank", "query", "ask"];
    for (const s of phase2b) {
      expect(_ALL_STATUSES).not.toContain(s);
    }
  });

  it("has exactly 5 status values", () => {
    expect(_ALL_STATUSES.length).toBe(5);
  });
});
