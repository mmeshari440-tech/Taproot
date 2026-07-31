import { useEffect, useReducer } from "react";
import { useParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/features/auth/AuthContext";
import { useCancelInvestigation, useInvestigation } from "@/features/investigate/hooks";
import {
  applyStepEvent,
  emptyTimeline,
  type StepEvent,
  type StepStatus,
  streamInvestigation,
  type TimelineState,
} from "@/lib/sse";

const TERMINAL = new Set(["DONE", "FAILED", "CANCELLED"]);

const STEP_VARIANT: Record<StepStatus, "neutral" | "success" | "danger"> = {
  running: "neutral",
  ok: "success",
  failed: "danger",
  skipped: "neutral",
};

function timelineReducer(state: TimelineState, event: StepEvent): TimelineState {
  return applyStepEvent(state, event);
}

export function InvestigationView() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [timeline, dispatch] = useReducer(timelineReducer, emptyTimeline);

  const investigation = useInvestigation(id, { poll: !timeline.done });
  const cancel = useCancelInvestigation();
  const status = investigation.data?.status;
  const isActive = status ? !TERMINAL.has(status) : true;

  const token = user?.access_token;
  useEffect(() => {
    if (!id || !token) return;
    // EventSource replays persisted steps (Last-Event-ID), so a refresh restores state.
    return streamInvestigation(id, token, (event) => dispatch(event));
  }, [id, token]);

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-primary">Investigation</h1>
        <div className="flex items-center gap-3">
          {status && <Badge variant={status === "FAILED" ? "danger" : "neutral"}>{status}</Badge>}
          {isActive && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => cancel.mutate(id, { onSuccess: () => investigation.refetch() })}
              disabled={cancel.isPending}
            >
              Cancel
            </Button>
          )}
        </div>
      </div>

      {investigation.data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="whitespace-pre-wrap text-sm">{investigation.data.error_text}</pre>
          </CardContent>
        </Card>
      )}

      <section aria-label="Step timeline" className="space-y-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Steps
        </h2>
        <ul className="divide-y divide-border rounded-lg border border-border">
          {timeline.steps.map((step) => (
            <li key={step.seq} className="px-4 py-2">
              <details>
                <summary className="flex cursor-pointer items-center justify-between">
                  <span>{step.title}</span>
                  <Badge variant={STEP_VARIANT[step.status]}>{step.status}</Badge>
                </summary>
                {step.summary && (
                  <p className="mt-1 text-sm text-muted-foreground">{step.summary}</p>
                )}
              </details>
            </li>
          ))}
          {timeline.steps.length === 0 && (
            <li className="px-4 py-2 text-muted-foreground">Waiting for steps…</li>
          )}
        </ul>
      </section>

      {timeline.error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {timeline.error}
        </div>
      )}
    </div>
  );
}
