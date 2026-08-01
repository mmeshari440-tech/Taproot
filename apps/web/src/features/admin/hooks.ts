import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type IntegrationKind,
  type IntegrationUpsert,
  type RepoKind,
} from "@/lib/api-client";

export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: api.listProjects });

export const useProject = (id: string) =>
  useQuery({ queryKey: ["project", id], queryFn: () => api.getProject(id) });

export const useRepos = (id: string) =>
  useQuery({ queryKey: ["repos", id], queryFn: () => api.listRepos(id) });

export const useIntegrations = (projectId: string, repoId: string) =>
  useQuery({
    queryKey: ["integrations", projectId, repoId],
    queryFn: () => api.listIntegrations(projectId, repoId),
  });

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createProject,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useSyncRepos(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.syncRepos(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["repos", projectId] }),
  });
}

export function useUpdateRepoKind(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { repoId: string; kind: RepoKind; orgPackagePrefixes?: string[] }) =>
      api.updateRepoKind(projectId, vars.repoId, {
        kind: vars.kind,
        org_package_prefixes: vars.orgPackagePrefixes,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["repos", projectId] }),
  });
}

export function usePutIntegration(projectId: string, repoId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { kind: IntegrationKind; body: IntegrationUpsert }) =>
      api.putIntegration(projectId, repoId, vars.kind, vars.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations", projectId, repoId] }),
  });
}

export function useTestIntegration(projectId: string, repoId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (kind: IntegrationKind) => api.testIntegration(projectId, repoId, kind),
    onSettled: () => qc.invalidateQueries({ queryKey: ["integrations", projectId, repoId] }),
  });
}
