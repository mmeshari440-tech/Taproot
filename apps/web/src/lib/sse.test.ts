import { describe, expect, it } from "vitest";

import { investigationStreamUrl } from "@/lib/api-client";
import { applyStepEvent, emptyTimeline, type StepEvent } from "@/lib/sse";

function fold(events: StepEvent[]) {
  return events.reduce(applyStepEvent, emptyTimeline);
}

describe("applyStepEvent", () => {
  it("adds a running step on step.start and resolves it on step.finish", () => {
    const state = fold([
      { type: "step.start", seq: 1, node: "normalize_query", title: "Normalizing" },
      { type: "step.finish", seq: 1, node: "normalize_query", status: "ok", summary: "done" },
    ]);
    expect(state.steps).toHaveLength(1);
    expect(state.steps[0].status).toBe("ok");
    expect(state.steps[0].summary).toBe("done");
  });

  it("keeps steps ordered by seq even if events arrive out of order", () => {
    const state = fold([
      { type: "step.start", seq: 2, node: "b", title: "B" },
      { type: "step.start", seq: 1, node: "a", title: "A" },
    ]);
    expect(state.steps.map((s) => s.seq)).toEqual([1, 2]);
  });

  it("captures error and done", () => {
    const state = fold([
      { type: "error", message: "Elastic timeout" },
      { type: "done" },
    ]);
    expect(state.error).toBe("Elastic timeout");
    expect(state.done).toBe(true);
  });

  it("marks a skipped step's status", () => {
    const state = fold([
      { type: "step.start", seq: 1, node: "sentry_enrich", title: "Sentry" },
      { type: "step.finish", seq: 1, node: "sentry_enrich", status: "skipped", summary: "not configured" },
    ]);
    expect(state.steps[0].status).toBe("skipped");
    expect(state.steps[0].summary).toBe("not configured");
  });
});

describe("investigationStreamUrl", () => {
  it("puts the token in the query string", () => {
    const url = investigationStreamUrl("abc", "tok/en");
    expect(url).toContain("/api/v1/investigations/abc/stream?access_token=tok%2Fen");
  });
});
