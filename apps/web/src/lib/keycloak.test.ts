import { describe, expect, it } from "vitest";

import { rolesFromAccessToken } from "@/lib/keycloak";

function fakeAccessToken(roles: string[]): string {
  const payload = btoa(JSON.stringify({ realm_access: { roles } }));
  return `header.${payload}.sig`;
}

describe("rolesFromAccessToken", () => {
  it("extracts realm roles from the access token", () => {
    expect(rolesFromAccessToken(fakeAccessToken(["platform-admin", "tech-user"]))).toEqual([
      "platform-admin",
      "tech-user",
    ]);
  });

  it("returns [] for undefined or malformed tokens", () => {
    expect(rolesFromAccessToken(undefined)).toEqual([]);
    expect(rolesFromAccessToken("not-a-jwt")).toEqual([]);
  });
});
