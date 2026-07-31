import { useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { IntegrationForm } from "@/features/admin/IntegrationForm";
import {
  useIntegrations,
  useProject,
  useRepos,
  useSyncRepos,
  useUpdateRepoKind,
} from "@/features/admin/hooks";
import { useRole } from "@/features/auth/useRole";
import { type IntegrationKind, type Repo, type RepoKind } from "@/lib/api-client";

const KINDS: IntegrationKind[] = ["ELASTIC", "SENTRY", "APPDYNAMICS"];
const REPO_KINDS: RepoKind[] = ["FE", "BE", "OTHER"];

function RepoRow({ projectId, repo }: { projectId: string; repo: Repo }) {
  const { isAdmin } = useRole();
  const update = useUpdateRepoKind(projectId);
  return (
    <li className="flex items-center justify-between gap-3 px-3 py-2">
      <span className="truncate">{repo.name}</span>
      {isAdmin ? (
        <select
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          value={repo.kind}
          onChange={(e) => update.mutate({ repoId: repo.id, kind: e.target.value as RepoKind })}
        >
          {REPO_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      ) : (
        <span className="text-xs text-muted-foreground">{repo.kind}</span>
      )}
    </li>
  );
}

export function ProjectDetailPage() {
  const { id = "" } = useParams();
  const { isAdmin } = useRole();
  const project = useProject(id);
  const repos = useRepos(id);
  const integrations = useIntegrations(id);
  const sync = useSyncRepos(id);

  const elastic = integrations.data?.find((i) => i.kind === "ELASTIC");
  const elasticOk = elastic?.status === "OK";

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <h1 className="text-2xl font-bold text-primary">{project.data?.name ?? "Project"}</h1>

      {!elasticOk && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          Elasticsearch is not verified — investigations are disabled until its connection test passes.
        </div>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Repositories</CardTitle>
          {isAdmin && (
            <Button size="sm" variant="outline" onClick={() => sync.mutate()} disabled={sync.isPending}>
              {sync.isPending ? "Syncing…" : "Sync from GitLab"}
            </Button>
          )}
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-border rounded-md border border-border">
            {(repos.data ?? []).map((r) => (
              <RepoRow key={r.id} projectId={id} repo={r} />
            ))}
            {repos.data?.length === 0 && (
              <li className="px-3 py-2 text-muted-foreground">No repos — sync from GitLab.</li>
            )}
          </ul>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Integrations</h2>
        {isAdmin ? (
          KINDS.map((kind) => (
            <IntegrationForm
              key={kind}
              projectId={id}
              kind={kind}
              existing={integrations.data?.find((i) => i.kind === kind)}
            />
          ))
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
            {(integrations.data ?? []).map((i) => (
              <li key={i.id} className="flex justify-between px-3 py-2">
                <span>{i.kind}</span>
                <span className="text-xs text-muted-foreground">{i.status}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
