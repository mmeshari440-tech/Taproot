import { useAuth } from "@/features/auth/AuthContext";

/** Role helpers derived from the token's realm roles (ARCHITECTURE.md §8.1). */
export function useRole() {
  const { roles, hasRole } = useAuth();
  return {
    roles,
    hasRole,
    isAdmin: hasRole("platform-admin"),
    isTechUser: hasRole("tech-user"),
  };
}
