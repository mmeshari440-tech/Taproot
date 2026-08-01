import { useMutation, useQuery } from "@tanstack/react-query";

import { api, type Project } from "@/lib/api-client";

/** Projects with at least one app that has a verified Elastic integration — the
 * only ones that can run investigations (PLAN.md §5.1, ADR-0002). The backend
 * computes `elastic_ok` per project, so no per-app fan-out is needed here. */
export function useHealthyElasticProjects(): { projects: Project[]; isLoading: boolean } {
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });
  const healthy = (projects.data ?? []).filter((p) => p.elastic_ok && p.is_active);
  return { projects: healthy, isLoading: projects.isLoading };
}

export function useSubmitInvestigation() {
  return useMutation({ mutationFn: api.submitInvestigation });
}

export function useInvestigation(id: string, { poll }: { poll: boolean }) {
  return useQuery({
    queryKey: ["investigation", id],
    queryFn: () => api.getInvestigation(id),
    refetchInterval: poll ? 3000 : false,
  });
}

export function useCancelInvestigation() {
  return useMutation({ mutationFn: (id: string) => api.cancelInvestigation(id) });
}
