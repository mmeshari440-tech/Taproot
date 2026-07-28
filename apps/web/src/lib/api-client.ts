/**
 * The single place the app talks HTTP (ARCHITECTURE.md §4).
 *
 * No `fetch` is allowed elsewhere. In T-11/T-19 this is replaced by a client
 * generated from the OpenAPI schema plus an auth interceptor that attaches the
 * Keycloak bearer token and triggers silent re-auth on 401.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface Health {
  status: string;
  version: string;
}

export async function getHealth(): Promise<Health> {
  const res = await fetch(`${API_BASE_URL}/health`);
  if (!res.ok) {
    throw new Error(`Health check failed: ${res.status}`);
  }
  return (await res.json()) as Health;
}
