import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { useHealthyElasticProjects, useSubmitInvestigation } from "@/features/investigate/hooks";

const WINDOWS = [1, 7, 14, 30];

export function InvestigatePage() {
  const navigate = useNavigate();
  const { projects, isLoading } = useHealthyElasticProjects();
  const submit = useSubmitInvestigation();

  const [projectId, setProjectId] = useState("");
  const [errorText, setErrorText] = useState("");
  const [windowDays, setWindowDays] = useState(7);

  async function onSubmit() {
    const chosen = projectId || projects[0]?.id;
    if (!chosen || !errorText.trim()) return;
    const inv = await submit.mutateAsync({
      project_id: chosen,
      error_text: errorText,
      time_window_days: windowDays,
    });
    navigate(`/investigations/${inv.id}`);
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <Card>
        <CardHeader>
          <CardTitle>New investigation</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label>Project</Label>
            <select
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
            >
              <option value="">
                {isLoading ? "Loading…" : projects.length ? "Select a project" : "No projects with healthy Elastic"}
              </option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              Only projects with a verified Elasticsearch integration are listed.
            </p>
          </div>

          <div className="space-y-1">
            <Label>Error message / stack trace</Label>
            <textarea
              className="min-h-32 w-full rounded-md border border-input bg-background p-3 text-sm"
              value={errorText}
              onChange={(e) => setErrorText(e.target.value)}
              placeholder="Paste the error you want investigated…"
            />
          </div>

          <div className="space-y-1">
            <Label>Time window</Label>
            <select
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
            >
              {WINDOWS.map((d) => (
                <option key={d} value={d}>
                  Last {d} day{d > 1 ? "s" : ""}
                </option>
              ))}
            </select>
          </div>

          {submit.isError && <p className="text-sm text-destructive">Could not start investigation.</p>}

          <Button
            onClick={() => void onSubmit()}
            disabled={submit.isPending || (!projectId && !projects.length) || !errorText.trim()}
          >
            {submit.isPending ? "Submitting…" : "Investigate"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
