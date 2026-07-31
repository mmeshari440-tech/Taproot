import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/features/admin/StatusBadge";
import { usePutIntegration, useTestIntegration } from "@/features/admin/hooks";
import { ApiError, type Integration, type IntegrationKind } from "@/lib/api-client";

const HINTS: Record<IntegrationKind, { externalIdLabel: string; needsClientId?: boolean }> = {
  ELASTIC: { externalIdLabel: "Index pattern (e.g. logs-*)" },
  SENTRY: { externalIdLabel: "org/project" },
  APPDYNAMICS: { externalIdLabel: "Application id", needsClientId: true },
};

export function IntegrationForm({
  projectId,
  kind,
  existing,
}: {
  projectId: string;
  kind: IntegrationKind;
  existing?: Integration;
}) {
  const put = usePutIntegration(projectId);
  const test = useTestIntegration(projectId);
  const hint = HINTS[kind];

  const [externalId, setExternalId] = useState(existing?.external_id ?? "");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? "");
  const [clientId, setClientId] = useState("");
  const [token, setToken] = useState("");
  const [replacing, setReplacing] = useState(!existing?.has_secret);
  const [error, setError] = useState<string | null>(null);

  async function saveAndTest() {
    setError(null);
    try {
      const config = hint.needsClientId && clientId ? { client_id: clientId } : {};
      await put.mutateAsync({
        kind,
        body: {
          external_id: externalId || null,
          base_url: baseUrl || null,
          token: replacing && token ? token : null,
          config,
        },
      });
      const result = await test.mutateAsync(kind);
      if (!result.ok) setError(result.error ?? "Connection failed");
      else {
        setToken("");
        setReplacing(false);
      }
    } catch (e) {
      // A failed test comes back as 422 with the provider's real message.
      if (e instanceof ApiError) {
        const detail = e.detail as { detail?: { error?: string } } | undefined;
        setError(detail?.detail?.error ?? e.message);
      } else {
        setError("Unexpected error");
      }
    }
  }

  const busy = put.isPending || test.isPending;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>{kind}</CardTitle>
        {existing ? <StatusBadge status={existing.status} /> : <StatusBadge status="UNVERIFIED" />}
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <Label>{hint.externalIdLabel}</Label>
          <Input value={externalId} onChange={(e) => setExternalId(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label>Base URL</Label>
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
        </div>
        {hint.needsClientId && (
          <div className="space-y-1">
            <Label>OAuth client id</Label>
            <Input value={clientId} onChange={(e) => setClientId(e.target.value)} />
          </div>
        )}
        <div className="space-y-1">
          <Label>Token / secret</Label>
          {existing?.has_secret && !replacing ? (
            <div className="flex items-center gap-2">
              <span className="font-mono text-muted-foreground">••••••••</span>
              <Button type="button" size="sm" variant="outline" onClick={() => setReplacing(true)}>
                Replace
              </Button>
            </div>
          ) : (
            <Input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="write-only — never displayed"
            />
          )}
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
        {existing?.last_checked_at && (
          <p className="text-xs text-muted-foreground">
            Last checked {new Date(existing.last_checked_at).toLocaleString()}
          </p>
        )}

        <Button onClick={() => void saveAndTest()} disabled={busy}>
          {busy ? "Testing…" : "Save & test connection"}
        </Button>
      </CardContent>
    </Card>
  );
}
