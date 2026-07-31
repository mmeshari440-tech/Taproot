import { type ReactNode } from "react";

import { useAuth } from "@/features/auth/AuthContext";

/** Gate a route on authentication and, optionally, a realm role.
 * Unauthenticated → redirect to Keycloak. Wrong role → 403 (never conflated). */
export function ProtectedRoute({ children, role }: { children: ReactNode; role?: string }) {
  const { isAuthenticated, isLoading, hasRole, login } = useAuth();

  if (isLoading) {
    return <p className="p-8 text-muted-foreground">Loading…</p>;
  }
  if (!isAuthenticated) {
    void login();
    return <p className="p-8 text-muted-foreground">Redirecting to sign in…</p>;
  }
  if (role && !hasRole(role)) {
    return (
      <div className="p-8">
        <h1 className="text-xl font-semibold text-destructive">403 — Forbidden</h1>
        <p className="text-muted-foreground">
          This page requires the <code>{role}</code> role.
        </p>
      </div>
    );
  }
  return <>{children}</>;
}
