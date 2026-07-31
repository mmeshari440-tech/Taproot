import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ProjectDetailPage } from "@/features/admin/ProjectDetailPage";
import { ProjectsPage } from "@/features/admin/ProjectsPage";
import { AuthProvider, useAuth } from "@/features/auth/AuthContext";
import { CallbackPage } from "@/features/auth/CallbackPage";
import { ProtectedRoute } from "@/features/auth/ProtectedRoute";
import { InvestigatePage } from "@/features/investigate/InvestigatePage";
import { InvestigationView } from "@/features/investigate/InvestigationView";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

function NavBar() {
  const { isAuthenticated, user, logout } = useAuth();
  return (
    <header className="flex items-center justify-between border-b border-border px-6 py-3">
      <div className="flex items-center gap-4">
        <Link to="/" className="text-lg font-bold text-primary">
          Taproot
        </Link>
        {isAuthenticated && (
          <nav className="flex gap-3 text-sm">
            <Link to="/" className="hover:underline">
              Projects
            </Link>
            <Link to="/investigate" className="hover:underline">
              Investigate
            </Link>
          </nav>
        )}
      </div>
      {isAuthenticated && (
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">{user?.profile.email}</span>
          <Button size="sm" variant="outline" onClick={() => void logout()}>
            Sign out
          </Button>
        </div>
      )}
    </header>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <NavBar />
          <Routes>
            <Route path="/callback" element={<CallbackPage />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <ProjectsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/projects/:id"
              element={
                <ProtectedRoute>
                  <ProjectDetailPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/investigate"
              element={
                <ProtectedRoute>
                  <InvestigatePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/investigations/:id"
              element={
                <ProtectedRoute>
                  <InvestigationView />
                </ProtectedRoute>
              }
            />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
