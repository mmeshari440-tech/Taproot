import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { userManager } from "@/lib/keycloak";

/** OIDC redirect landing (`/callback`): completes the code exchange, then home. */
export function CallbackPage() {
  const navigate = useNavigate();
  useEffect(() => {
    userManager
      .signinRedirectCallback()
      .then(() => navigate("/", { replace: true }))
      .catch(() => navigate("/", { replace: true }));
  }, [navigate]);
  return <p className="p-8 text-muted-foreground">Completing sign-in…</p>;
}
