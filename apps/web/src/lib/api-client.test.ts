import { afterEach, describe, expect, it, vi } from "vitest";

import {
  api,
  ApiError,
  setTokenProvider,
  setUnauthorizedHandler,
} from "@/lib/api-client";

afterEach(() => {
  vi.restoreAllMocks();
  setTokenProvider(() => null);
  setUnauthorizedHandler(() => {});
});

function mockFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(status === 204 ? null : JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
}

describe("api-client", () => {
  it("attaches the bearer token from the provider", async () => {
    setTokenProvider(() => "tok-123");
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify([]), { status: 200 })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.listProjects();

    const init = fetchMock.mock.calls[0]?.[1];
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer tok-123");
  });

  it("invokes the unauthorized handler and throws on 401", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    mockFetch(401, { detail: "expired" });

    await expect(api.listProjects()).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("surfaces the provider error message from a 422 detail", async () => {
    mockFetch(422, { detail: { error: "invalid apikey" } });
    await expect(api.testIntegration("p1", "ELASTIC")).rejects.toMatchObject({
      status: 422,
      message: "invalid apikey",
    });
  });
});
