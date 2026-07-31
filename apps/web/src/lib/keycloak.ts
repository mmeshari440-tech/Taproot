/**
 * Keycloak / OIDC wiring (ARCHITECTURE.md §8.1, T-08).
 *
 * The FE is a PUBLIC client — Authorization Code + PKCE, no client secret in the
 * bundle. `oidc-client-ts` handles the redirect flow, token storage, and silent
 * renew.
 */
import { UserManager, WebStorageStateStore, type UserManagerSettings } from "oidc-client-ts";

const settings: UserManagerSettings = {
  authority: import.meta.env.VITE_KEYCLOAK_AUTHORITY ?? "",
  client_id: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "taproot-web",
  redirect_uri: `${window.location.origin}/callback`,
  post_logout_redirect_uri: window.location.origin,
  response_type: "code",
  scope: "openid profile email",
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.localStorage }),
};

export const userManager = new UserManager(settings);

/** Realm roles live in the ACCESS token (`realm_access.roles`), not the id token. */
export function rolesFromAccessToken(accessToken: string | undefined): string[] {
  if (!accessToken) return [];
  const [, payload] = accessToken.split(".");
  if (!payload) return [];
  try {
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as {
      realm_access?: { roles?: string[] };
    };
    return json.realm_access?.roles ?? [];
  } catch {
    return [];
  }
}
