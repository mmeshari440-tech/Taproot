/**
 * Keycloak / OIDC wiring (ARCHITECTURE.md §8.1).
 *
 * Implemented in T-08 with oidc-client-ts (Authorization Code + PKCE). The FE is
 * a PUBLIC client — no client secret ever ships in the bundle. Stub for now.
 */

export interface AuthConfig {
  authority: string;
  clientId: string;
}

export const authConfig: AuthConfig = {
  authority: import.meta.env.VITE_KEYCLOAK_AUTHORITY ?? "",
  clientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "taproot-web",
};
