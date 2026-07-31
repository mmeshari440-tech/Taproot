import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCreateProject, useProjects } from "@/features/admin/hooks";
import { useRole } from "@/features/auth/useRole";
import { api, type GitLabGroup } from "@/lib/api-client";

function CreateProject() {
  const [search, setSearch] = useState("");
  const groups = useMutation({ mutationFn: (q: string) => api.searchGitLabGroups(q) });
  const create = useCreateProject();

  function pick(g: GitLabGroup) {
    create.mutate({ name: g.name, gitlab_group_path: g.full_path, gitlab_group_id: g.id });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New project — pick a GitLab group</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label>Search groups</Label>
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && groups.mutate(search)}
              placeholder="e.g. payments"
            />
          </div>
          <Button onClick={() => groups.mutate(search)} disabled={groups.isPending}>
            Search
          </Button>
        </div>
        {groups.isError && <p className="text-sm text-destructive">GitLab search failed.</p>}
        <ul className="divide-y divide-border rounded-md border border-border">
          {(groups.data ?? []).map((g) => (
            <li key={g.id} className="flex items-center justify-between px-3 py-2">
              <span>
                {g.name} <span className="text-muted-foreground">/ {g.full_path}</span>
              </span>
              <Button size="sm" variant="outline" onClick={() => pick(g)} disabled={create.isPending}>
                Create
              </Button>
            </li>
          ))}
        </ul>
        {create.isError && <p className="text-sm text-destructive">Could not create project.</p>}
      </CardContent>
    </Card>
  );
}

export function ProjectsPage() {
  const { isAdmin } = useRole();
  const { data: projects, isLoading } = useProjects();

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <h1 className="text-2xl font-bold text-primary">Projects</h1>
      {isAdmin && <CreateProject />}

      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {(projects ?? []).map((p) => (
            <li key={p.id} className="flex items-center justify-between px-4 py-3">
              <Link to={`/projects/${p.id}`} className="font-medium hover:underline">
                {p.name}
              </Link>
              <span className="text-xs text-muted-foreground">
                {p.is_active ? p.gitlab_group_path : "inactive"}
              </span>
            </li>
          ))}
          {projects?.length === 0 && (
            <li className="px-4 py-3 text-muted-foreground">No projects yet.</li>
          )}
        </ul>
      )}
    </div>
  );
}
