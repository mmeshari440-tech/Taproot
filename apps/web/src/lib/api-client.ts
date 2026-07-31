/**
 * The single place the app talks HTTP (ARCHITECTURE.md §4).
 *
 * No `fetch` is allowed elsewhere. The bearer token is supplied by the auth layer
 * via {@link setTokenProvider}; a 401 invokes {@link setUnauthorizedHandler} so the
 * auth layer can silently re-auth. Types here are hand-written for Sprint 1 and
 * will be replaced by the OpenAPI-generated client (see `types/generated/`).
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

let tokenProvider: () => string | null = () => null;
let unauthorizedHandler: () => void = () => {};

export function setTokenProvider(fn: () => string | null): void {
  tokenProvider = fn;
}

export function setUnauthorizedHandler(fn: () => void): void {
  unauthorizedHandler = fn;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = tokenProvider();
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });

  if (res.status === 401) {
    unauthorizedHandler();
    throw new ApiError(401, "Unauthorized");
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => undefined);
    const message =
      (detail && typeof detail === "object" && "detail" in detail
        ? extractMessage((detail as { detail: unknown }).detail)
        : undefined) ?? `Request failed: ${res.status}`;
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function extractMessage(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "error" in detail) {
    const err = (detail as { error?: unknown }).error;
    if (typeof err === "string") return err;
  }
  return undefined;
}

// --- Types (mirror the backend Pydantic schemas) ---------------------------
export type RepoKind = "FE" | "BE" | "OTHER";
export type IntegrationKind = "ELASTIC" | "SENTRY" | "APPDYNAMICS";
export type IntegrationStatus = "UNVERIFIED" | "OK" | "FAILED";

export interface Me {
  id: string;
  sub: string;
  email: string;
  display_name: string | null;
  roles: string[];
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  gitlab_group_path: string | null;
  is_active: boolean;
}

export interface GitLabGroup {
  id: number;
  name: string;
  full_path: string;
  web_url: string | null;
}

export interface Repo {
  id: string;
  gitlab_project_id: number;
  name: string;
  kind: RepoKind;
  default_branch: string;
  web_url: string | null;
  org_package_prefixes: string[];
}

export interface Integration {
  id: string;
  kind: IntegrationKind;
  external_id: string | null;
  base_url: string | null;
  status: IntegrationStatus;
  last_checked_at: string | null;
  last_error: string | null;
  has_secret: boolean;
}

export interface ConnectionTestResult {
  ok: boolean;
  latency_ms: number;
  detail: string | null;
  error: string | null;
}

export interface IntegrationUpsert {
  external_id?: string | null;
  base_url?: string | null;
  token?: string | null;
  config?: Record<string, unknown>;
}

// --- Endpoints --------------------------------------------------------------
export const api = {
  getMe: () => request<Me>("/api/v1/me"),

  listProjects: () => request<Project[]>("/api/v1/projects"),
  getProject: (id: string) => request<Project>(`/api/v1/projects/${id}`),
  createProject: (body: { name: string; gitlab_group_path?: string; gitlab_group_id?: number }) =>
    request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify(body) }),
  deactivateProject: (id: string) =>
    request<Project>(`/api/v1/projects/${id}/deactivate`, { method: "POST" }),

  searchGitLabGroups: (search: string) =>
    request<GitLabGroup[]>(`/api/v1/projects/gitlab-groups?search=${encodeURIComponent(search)}`),

  listRepos: (projectId: string) => request<Repo[]>(`/api/v1/projects/${projectId}/repos`),
  syncRepos: (projectId: string) =>
    request<Repo[]>(`/api/v1/projects/${projectId}/sync-repos`, { method: "POST" }),
  updateRepoKind: (projectId: string, repoId: string, body: { kind: RepoKind; org_package_prefixes?: string[] }) =>
    request<Repo>(`/api/v1/projects/${projectId}/repos/${repoId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  listIntegrations: (projectId: string) =>
    request<Integration[]>(`/api/v1/projects/${projectId}/integrations`),
  putIntegration: (projectId: string, kind: IntegrationKind, body: IntegrationUpsert) =>
    request<Integration>(`/api/v1/projects/${projectId}/integrations/${kind}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  testIntegration: (projectId: string, kind: IntegrationKind) =>
    request<ConnectionTestResult>(`/api/v1/projects/${projectId}/integrations/${kind}/test`, {
      method: "POST",
    }),
};
