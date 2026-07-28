/**
 * Typed EventSource wrapper for the investigation step stream (ARCHITECTURE.md §4).
 *
 * Implemented in T-18/T-19: reconnect with Last-Event-ID, typed step events, and
 * reconciliation against GET /investigations/{id} on mount. Stub for now.
 */

export type StepEventType =
  | "step.start"
  | "step.progress"
  | "step.finish"
  | "result"
  | "error"
  | "done";

export interface StepEvent {
  type: StepEventType;
  seq?: number;
  node?: string;
  [key: string]: unknown;
}

// TODO(T-18): implement subscribe() with reconnect + Last-Event-ID replay.
