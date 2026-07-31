/**
 * Investigation step stream (ARCHITECTURE.md §4, T-18/T-19).
 *
 * The browser's `EventSource` handles reconnect + `Last-Event-ID` natively (we
 * emit `id:` per step), so this is a thin typed wrapper. The timeline reducer is
 * pure so it can be unit-tested without a live stream.
 */
import { investigationStreamUrl } from "@/lib/api-client";

export type StepStatus = "running" | "ok" | "failed" | "skipped";

export interface StepEvent {
  type: "step.start" | "step.progress" | "step.finish" | "result" | "error" | "done";
  seq?: number;
  node?: string;
  title?: string;
  status?: StepStatus;
  summary?: string | null;
  message?: string;
  [key: string]: unknown;
}

export interface TimelineStep {
  seq: number;
  node: string;
  title: string;
  status: StepStatus;
  summary?: string | null;
}

export interface TimelineState {
  steps: TimelineStep[];
  error: string | null;
  done: boolean;
}

export const emptyTimeline: TimelineState = { steps: [], error: null, done: false };

/** Fold one SSE event into timeline state (pure — the reducer for the UI). */
export function applyStepEvent(state: TimelineState, event: StepEvent): TimelineState {
  switch (event.type) {
    case "step.start": {
      if (event.seq === undefined || !event.node) return state;
      const step: TimelineStep = {
        seq: event.seq,
        node: event.node,
        title: event.title ?? event.node,
        status: "running",
      };
      const steps = upsert(state.steps, step);
      return { ...state, steps };
    }
    case "step.finish": {
      if (event.seq === undefined) return state;
      const steps = state.steps.map((s) =>
        s.seq === event.seq
          ? { ...s, status: event.status ?? "ok", summary: event.summary ?? s.summary }
          : s,
      );
      return { ...state, steps };
    }
    case "error":
      return { ...state, error: event.message ?? "Investigation failed" };
    case "done":
      return { ...state, done: true };
    default:
      return state;
  }
}

function upsert(steps: TimelineStep[], step: TimelineStep): TimelineStep[] {
  const idx = steps.findIndex((s) => s.seq === step.seq);
  if (idx === -1) return [...steps, step].sort((a, b) => a.seq - b.seq);
  const copy = steps.slice();
  copy[idx] = { ...copy[idx], ...step };
  return copy;
}

/** Open the step stream. Returns a cleanup function that closes it. */
export function streamInvestigation(
  id: string,
  token: string,
  onEvent: (event: StepEvent) => void,
): () => void {
  const source = new EventSource(investigationStreamUrl(id, token));
  source.onmessage = (msg: MessageEvent<string>) => {
    try {
      const event = JSON.parse(msg.data) as StepEvent;
      onEvent(event);
      if (event.type === "done") source.close();
    } catch {
      /* ignore malformed frames */
    }
  };
  source.onerror = () => {
    /* EventSource auto-reconnects with Last-Event-ID; nothing to do here */
  };
  return () => source.close();
}
