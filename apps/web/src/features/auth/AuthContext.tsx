/**
 * Auth context (T-08): wraps oidc-client-ts, exposes the session + roles, and
 * wires the bearer token / 401 handler into the API client.
 */
import { type User } from "oidc-client-ts";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";

import { setTokenProvider, setUnauthorizedHandler } from "@/lib/api-client";
import { rolesFromAccessToken, userManager } from "@/lib/keycloak";

export interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  roles: string[];
  hasRole: (role: string) => boolean;
  login: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const userRef = useRef<User | null>(null);

  const applyUser = useCallback((u: User | null) => {
    userRef.current = u;
    setUser(u);
  }, []);

  const login = useCallback(() => userManager.signinRedirect(), []);
  const logout = useCallback(() => userManager.signoutRedirect(), []);

  useEffect(() => {
    // Token + 401 wiring for the API client (no fetch happens outside it).
    setTokenProvider(() => userRef.current?.access_token ?? null);
    setUnauthorizedHandler(() => {
      void userManager.signinSilent().catch(() => userManager.signinRedirect());
    });

    void userManager.getUser().then((u) => {
      applyUser(u && !u.expired ? u : null);
      setIsLoading(false);
    });

    const onLoaded = (u: User) => applyUser(u);
    const onUnloaded = () => applyUser(null);
    userManager.events.addUserLoaded(onLoaded);
    userManager.events.addUserUnloaded(onUnloaded);
    userManager.events.addSilentRenewError(() => applyUser(null));
    return () => {
      userManager.events.removeUserLoaded(onLoaded);
      userManager.events.removeUserUnloaded(onUnloaded);
    };
  }, [applyUser]);

  const roles = user ? rolesFromAccessToken(user.access_token) : [];
  const value: AuthState = {
    user,
    isLoading,
    isAuthenticated: Boolean(user) && !user?.expired,
    roles,
    hasRole: (role: string) => roles.includes(role),
    login,
    logout,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
