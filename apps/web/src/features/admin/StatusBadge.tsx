import { Badge } from "@/components/ui/badge";
import { type IntegrationStatus } from "@/lib/api-client";

const MAP: Record<IntegrationStatus, { label: string; variant: "neutral" | "success" | "danger" }> = {
  OK: { label: "OK", variant: "success" },
  FAILED: { label: "Failed", variant: "danger" },
  UNVERIFIED: { label: "Unverified", variant: "neutral" },
};

export function StatusBadge({ status }: { status: IntegrationStatus }) {
  const { label, variant } = MAP[status];
  return <Badge variant={variant}>{label}</Badge>;
}
