/**
 * AtlasCore API client.
 *
 * SECURITY:
 * - Access tokens are kept in JavaScript memory (module-level variable).
 *   They are NEVER stored in localStorage or sessionStorage.
 * - Refresh tokens are in HttpOnly cookies (set by the server).
 * - CSRF tokens are read from the 'csrf_token' cookie (set by server,
 *   NOT HttpOnly) and sent as the X-CSRF-Token header on state-changing
 *   requests.
 * - All state-changing requests include the X-CSRF-Token header.
 * - On 401: attempt token refresh, retry once, then clear state.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8100";

// ---------------------------------------------------------------------------
// In-memory access token storage
// ---------------------------------------------------------------------------

let _accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  _accessToken = token;
}

export function getAccessToken(): string | null {
  return _accessToken;
}

export function clearAccessToken(): void {
  _accessToken = null;
}

// ---------------------------------------------------------------------------
// CSRF cookie reading
// ---------------------------------------------------------------------------

function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith("csrf_token="));
  return match ? match.split("=")[1] : null;
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

interface ApiOptions {
  method?: string;
  body?: unknown;
  withCredentials?: boolean;
  skipAuth?: boolean;
}

class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const {
    method = "GET",
    body,
    withCredentials = true,
    skipAuth = false,
  } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // Access token (memory only)
  if (!skipAuth && _accessToken) {
    headers["Authorization"] = `Bearer ${_accessToken}`;
  }

  // CSRF token for state-changing requests
  const isStateChanging = ["POST", "PUT", "PATCH", "DELETE"].includes(
    method.toUpperCase(),
  );
  if (isStateChanging && withCredentials) {
    const csrf = getCsrfToken();
    if (csrf) {
      headers["X-CSRF-Token"] = csrf;
    }
  }

  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    method,
    headers,
    credentials: withCredentials ? "include" : "omit",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (response.status === 204) {
    return undefined as unknown as T;
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    // On 401: try refresh once, then re-throw
    if (response.status === 401 && !skipAuth && path !== "/auth/refresh") {
      const refreshed = await tryRefresh();
      if (refreshed) {
        // Retry with new access token
        return apiFetch<T>(path, options);
      }
    }
    throw new ApiError(response.status, data?.detail ?? "Unknown error");
  }

  return data as T;
}

// ---------------------------------------------------------------------------
// Token refresh
// ---------------------------------------------------------------------------

let _refreshPromise: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = (async () => {
    try {
      const data = await apiFetch<{ access_token: string }>(
        "/auth/refresh",
        { method: "POST", skipAuth: true },
      );
      setAccessToken(data.access_token);
      return true;
    } catch {
      clearAccessToken();
      return false;
    } finally {
      _refreshPromise = null;
    }
  })();
  return _refreshPromise;
}

// ---------------------------------------------------------------------------
// Auth API
// ---------------------------------------------------------------------------

export interface Organisation {
  id: string;
  slug: string;
  display_name: string;
  org_role: string | null;
}

export interface LoginStep1Response {
  organisations: Organisation[];
  message: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface MeResponse {
  user_id: string;
  email: string;
  full_name: string;
  organisation_id: string;
  organisation_slug: string;
  org_role: string | null;
  workspace_id: string | null;
  is_platform_admin: boolean;
}

export const auth = {
  register: (body: {
    email: string;
    password: string;
    full_name: string;
    organisation_name: string;
    organisation_slug: string;
  }) => apiFetch<{ message: string }>("/auth/register", { method: "POST", body, skipAuth: true }),

  login: (body: { email: string; password: string }) =>
    apiFetch<LoginStep1Response>("/auth/login", {
      method: "POST",
      body,
      skipAuth: true,
    }),

  selectOrg: (body: { organisation_id: string }) =>
    apiFetch<TokenResponse>("/auth/select-organisation", {
      method: "POST",
      body,
      skipAuth: true,
    }),

  refresh: () =>
    apiFetch<TokenResponse>("/auth/refresh", { method: "POST", skipAuth: true }),

  logout: () =>
    apiFetch<{ message: string }>("/auth/logout", { method: "POST" }),

  logoutAll: () =>
    apiFetch<{ message: string }>("/auth/logout-all", { method: "POST" }),

  me: () => apiFetch<MeResponse>("/auth/me"),
};

// ---------------------------------------------------------------------------
// Phase 1B — Invitations
// ---------------------------------------------------------------------------

export interface Invitation {
  id: string;
  organisation_id: string;
  workspace_id: string | null;
  invited_email: string;
  org_role: string | null;
  workspace_role: string | null;
  created_by_user_id: string | null;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
  is_active: boolean;
}

export interface InvitationCreated {
  invitation: Invitation;
  raw_token: string;
  delivery_note: string;
}

export const invitations = {
  create: (body: {
    invited_email: string;
    org_role?: string | null;
    workspace_id?: string | null;
    workspace_role?: string | null;
    expires_in_hours?: number;
  }) => apiFetch<InvitationCreated>("/invitations", { method: "POST", body }),

  list: (params?: { active_only?: boolean; page?: number; page_size?: number }) => {
    const qs = new URLSearchParams();
    if (params?.active_only) qs.set("active_only", "true");
    if (params?.page) qs.set("page", String(params.page));
    if (params?.page_size) qs.set("page_size", String(params.page_size));
    const q = qs.toString();
    return apiFetch<Invitation[]>(`/invitations${q ? "?" + q : ""}`);
  },

  revoke: (id: string) =>
    apiFetch<Invitation>(`/invitations/${id}/revoke`, { method: "POST", body: {} }),

  accept: (token: string) =>
    apiFetch<Invitation>("/invitations/accept", { method: "POST", body: { token } }),
};

// ---------------------------------------------------------------------------
// Phase 1B — Teams
// ---------------------------------------------------------------------------

export interface Team {
  id: string;
  organisation_id: string;
  workspace_id: string | null;
  name: string;
  description: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface TeamMember {
  id: string;
  team_id: string;
  user_id: string;
  organisation_id: string;
  created_at: string;
}

export interface PaginatedTeams {
  items: Team[];
  total: number;
  page: number;
  page_size: number;
}

export const teams = {
  create: (body: { name: string; description?: string | null; workspace_id?: string | null }) =>
    apiFetch<Team>("/teams", { method: "POST", body }),

  list: (params?: { workspace_id?: string; page?: number; page_size?: number }) => {
    const qs = new URLSearchParams();
    if (params?.workspace_id) qs.set("workspace_id", params.workspace_id);
    if (params?.page) qs.set("page", String(params.page));
    if (params?.page_size) qs.set("page_size", String(params.page_size));
    const q = qs.toString();
    return apiFetch<PaginatedTeams>(`/teams${q ? "?" + q : ""}`);
  },

  get: (id: string) => apiFetch<Team>(`/teams/${id}`),

  update: (id: string, body: { name?: string; description?: string }) =>
    apiFetch<Team>(`/teams/${id}`, { method: "PATCH", body }),

  delete: (id: string) => apiFetch<void>(`/teams/${id}`, { method: "DELETE" }),

  listMembers: (teamId: string) => apiFetch<TeamMember[]>(`/teams/${teamId}/members`),

  addMember: (teamId: string, userId: string) =>
    apiFetch<TeamMember>(`/teams/${teamId}/members`, {
      method: "POST",
      body: { user_id: userId },
    }),

  removeMember: (teamId: string, userId: string) =>
    apiFetch<void>(`/teams/${teamId}/members/${userId}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------------------
// Phase 1B — Service Accounts & API Keys
// ---------------------------------------------------------------------------

export interface ServiceAccount {
  id: string;
  organisation_id: string;
  workspace_id: string | null;
  name: string;
  description: string | null;
  is_active: boolean;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  disabled_at: string | null;
  last_used_at: string | null;
}

export interface ApiKey {
  id: string;
  service_account_id: string;
  organisation_id: string;
  workspace_id: string | null;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  is_active: boolean;
}

export interface ApiKeyCreated {
  api_key: ApiKey;
  raw_key: string;
  warning: string;
}

export interface PaginatedServiceAccounts {
  items: ServiceAccount[];
  total: number;
  page: number;
  page_size: number;
}

export interface PaginatedApiKeys {
  items: ApiKey[];
  total: number;
  page: number;
  page_size: number;
}

export const serviceAccounts = {
  create: (body: { name: string; description?: string | null; workspace_id?: string | null }) =>
    apiFetch<ServiceAccount>("/service-accounts", { method: "POST", body }),

  list: (params?: { page?: number; page_size?: number }) => {
    const qs = new URLSearchParams();
    if (params?.page) qs.set("page", String(params.page));
    if (params?.page_size) qs.set("page_size", String(params.page_size));
    const q = qs.toString();
    return apiFetch<PaginatedServiceAccounts>(`/service-accounts${q ? "?" + q : ""}`);
  },

  get: (id: string) => apiFetch<ServiceAccount>(`/service-accounts/${id}`),

  update: (id: string, body: { description?: string }) =>
    apiFetch<ServiceAccount>(`/service-accounts/${id}`, { method: "PATCH", body }),

  disable: (id: string) =>
    apiFetch<ServiceAccount>(`/service-accounts/${id}/disable`, { method: "POST", body: {} }),

  enable: (id: string) =>
    apiFetch<ServiceAccount>(`/service-accounts/${id}/enable`, { method: "POST", body: {} }),

  createApiKey: (
    saId: string,
    body: { name: string; scopes: string[]; expires_in_days?: number | null },
  ) => apiFetch<ApiKeyCreated>(`/service-accounts/${saId}/api-keys`, { method: "POST", body }),

  listApiKeys: (saId: string, params?: { page?: number; page_size?: number }) => {
    const qs = new URLSearchParams();
    if (params?.page) qs.set("page", String(params.page));
    if (params?.page_size) qs.set("page_size", String(params.page_size));
    const q = qs.toString();
    return apiFetch<PaginatedApiKeys>(
      `/service-accounts/${saId}/api-keys${q ? "?" + q : ""}`,
    );
  },

  revokeApiKey: (saId: string, keyId: string, reason?: string) =>
    apiFetch<ApiKey>(`/service-accounts/${saId}/api-keys/${keyId}/revoke`, {
      method: "POST",
      body: { reason: reason ?? null },
    }),

  rotateApiKey: (saId: string, keyId: string) =>
    apiFetch<ApiKeyCreated>(`/service-accounts/${saId}/api-keys/${keyId}/rotate`, {
      method: "POST",
      body: {},
    }),
};

// ---------------------------------------------------------------------------
// Workspaces
// ---------------------------------------------------------------------------

export interface Workspace {
  id: string;
  organisation_id: string;
  slug: string;
  display_name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export const workspaces = {
  list: () => apiFetch<Workspace[]>("/workspaces"),
  get: (id: string) => apiFetch<Workspace>(`/workspaces/${id}`),
};

// ---------------------------------------------------------------------------
// Phase 2A — Knowledge
// ---------------------------------------------------------------------------

export interface KnowledgeSource {
  id: string;
  organisation_id: string;
  workspace_id: string;
  source_type: string;
  display_name: string;
  description: string | null;
  is_active: boolean;
  configuration: Record<string, unknown>;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocument {
  id: string;
  organisation_id: string;
  workspace_id: string;
  source_id: string;
  original_filename: string;
  media_type: string;
  is_archived: boolean;
  archived_at: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocumentVersion {
  id: string;
  document_id: string;
  organisation_id: string;
  workspace_id: string;
  version_number: number;
  content_sha256: string;
  size_bytes: number;
  media_type: string;
  created_by_user_id: string | null;
  created_at: string;
}

export interface KnowledgeIngestionJob {
  id: string;
  version_id: string;
  document_id: string;
  organisation_id: string;
  workspace_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  idempotency_key: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  result_metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeUploadResponse {
  document: KnowledgeDocument;
  version: KnowledgeDocumentVersion;
  ingestion_job: KnowledgeIngestionJob;
}

export const knowledge = {
  // Sources
  createSource: (
    workspaceId: string,
    body: {
      source_type: string;
      display_name: string;
      description?: string | null;
      configuration?: Record<string, unknown>;
    },
  ) =>
    apiFetch<KnowledgeSource>(
      `/knowledge/workspaces/${workspaceId}/sources`,
      { method: "POST", body },
    ),

  listSources: (workspaceId: string, includeInactive = false) => {
    const qs = includeInactive ? "?include_inactive=true" : "";
    return apiFetch<KnowledgeSource[]>(
      `/knowledge/workspaces/${workspaceId}/sources${qs}`,
    );
  },

  getSource: (workspaceId: string, sourceId: string) =>
    apiFetch<KnowledgeSource>(
      `/knowledge/workspaces/${workspaceId}/sources/${sourceId}`,
    ),

  updateSource: (
    workspaceId: string,
    sourceId: string,
    body: {
      display_name?: string;
      description?: string | null;
      configuration?: Record<string, unknown>;
      is_active?: boolean;
    },
  ) =>
    apiFetch<KnowledgeSource>(
      `/knowledge/workspaces/${workspaceId}/sources/${sourceId}`,
      { method: "PATCH", body },
    ),

  // Documents
  listDocuments: (
    workspaceId: string,
    params?: { source_id?: string; include_archived?: boolean },
  ) => {
    const qs = new URLSearchParams();
    if (params?.source_id) qs.set("source_id", params.source_id);
    if (params?.include_archived) qs.set("include_archived", "true");
    const q = qs.toString();
    return apiFetch<KnowledgeDocument[]>(
      `/knowledge/workspaces/${workspaceId}/documents${q ? "?" + q : ""}`,
    );
  },

  uploadDocument: (workspaceId: string, sourceId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);

    // Custom fetch — FormData must not set Content-Type (browser sets multipart boundary)
    const headers: Record<string, string> = {};
    if (_accessToken) headers["Authorization"] = `Bearer ${_accessToken}`;
    const csrf = getCsrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;

    return fetch(
      `${API_BASE}/api/v1/knowledge/workspaces/${workspaceId}/sources/${sourceId}/upload`,
      { method: "POST", headers, credentials: "include", body: formData },
    ).then(async (res) => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok)
        throw new ApiError(res.status, data?.detail ?? "Upload failed");
      return data as KnowledgeUploadResponse;
    });
  },

  archiveDocument: (workspaceId: string, documentId: string) =>
    apiFetch<KnowledgeDocument>(
      `/knowledge/workspaces/${workspaceId}/documents/${documentId}/archive`,
      { method: "POST", body: {} },
    ),

  listVersions: (workspaceId: string, documentId: string) =>
    apiFetch<KnowledgeDocumentVersion[]>(
      `/knowledge/workspaces/${workspaceId}/documents/${documentId}/versions`,
    ),

  // Jobs
  listJobs: (workspaceId: string, documentId?: string) => {
    const qs = documentId ? `?document_id=${documentId}` : "";
    return apiFetch<KnowledgeIngestionJob[]>(
      `/knowledge/workspaces/${workspaceId}/jobs${qs}`,
    );
  },

  getJob: (workspaceId: string, jobId: string) =>
    apiFetch<KnowledgeIngestionJob>(
      `/knowledge/workspaces/${workspaceId}/jobs/${jobId}`,
    ),

  retryJob: (workspaceId: string, jobId: string) =>
    apiFetch<KnowledgeIngestionJob>(
      `/knowledge/workspaces/${workspaceId}/jobs/${jobId}/retry`,
      { method: "POST", body: {} },
    ),

  // Phase 2B: hybrid retrieval search
  search: (
    workspaceId: string,
    params: {
      query: string;
      limit?: number;
      source_ids?: string[];
      document_ids?: string[];
      include_archived?: boolean;
    },
  ) =>
    apiFetch<SearchResponse>(
      `/knowledge/workspaces/${workspaceId}/search`,
      { method: "POST", body: params },
    ),

  // Phase 2C: grounded Q&A
  answer: (
    workspaceId: string,
    params: {
      question: string;
      top_k?: number;
    },
  ) =>
    apiFetch<AnswerResponse>(
      `/knowledge/workspaces/${workspaceId}/answer`,
      { method: "POST", body: params },
    ),
};

// ---------------------------------------------------------------------------
// Phase 2B: Retrieval search types
// ---------------------------------------------------------------------------

export interface SearchResult {
  chunk_id: string;
  document_id: string;
  document_version_id: string;
  source_id: string;
  document_title: string;
  source_name: string;
  version_number: number;
  chunk_index: number;
  content: string;
  lexical_score: number | null;
  lexical_rank: number | null;
  vector_score: number | null;
  vector_rank: number | null;
  hybrid_score: number;
  metadata: Record<string, unknown>;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  query_length: number;
}

// ---------------------------------------------------------------------------
// Phase 2C: Grounded answering types
// ---------------------------------------------------------------------------

export interface AnswerCitation {
  citation_id: string;       // "E1", "E2", …
  label: number;             // numeric label as used in answer_text [1], [2], …
  source_id: string;
  document_id: string;
  document_version_id: string;
  chunk_id: string;
  source_name: string;       // server-controlled provenance
  document_title: string;    // server-controlled provenance
  version_number: number;
  chunk_index: number;
  excerpt: string | null;    // short bounded excerpt
}

export interface AnswerResponse {
  status: "answer" | "abstain_no_evidence" | "abstain_weak_evidence" | "provider_failure";
  answer_text: string;
  citations: AnswerCitation[];
  evidence_band: "high" | "medium" | "low" | "none";
  provider: string;
  model: string;
  limitations: string[];
  suspicious_count: number;
}

export { ApiError };
