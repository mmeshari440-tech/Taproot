import { useMutation, useQueries, useQuery } from "@tanstack/react-query";

import { api, type Project } from "@/lib/api-client";

/** Projects whose Elasticsearch integration is verified — the only ones that can
 * run investigations (PLAN.md §5.1). */
export function useHealthyElasticProjects(): { projects: Project[]; isLoading: boolean } {
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });
  const list = projects.data ?? [];
  const integrations = useQueries({
    queries: list.map((p) => ({
      queryKey: ["integrations", p.id],
      queryFn: () => api.listIntegrations(p.id),
    })),
  });
  const healthy = list.filter((_p, i) =>
    (integrations[i]?.data ?? []).some((x) => x.kind === "ELASTIC" && x.status === "OK"),
  );
  const isLoading = projects.isLoading || integrations.some((q) => q.isLoading);
  return { projects: healthy, isLoading };
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
