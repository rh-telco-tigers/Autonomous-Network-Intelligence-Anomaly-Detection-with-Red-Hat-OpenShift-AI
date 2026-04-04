import { Badge } from "@/components/ui/badge";

type StatusBadgeProps = {
  value: string;
};

export function StatusBadge({ value }: StatusBadgeProps) {
  const normalized = value.toLowerCase();
  const variant = normalized.includes("verified") ||
    normalized.includes("done") ||
    normalized.includes("ok") ||
    normalized.includes("ready") ||
    normalized.includes("healthy") ||
    normalized.includes("closed")
    ? "success"
    : normalized.includes("critical") || normalized.includes("failed") || normalized.includes("error")
      ? "danger"
      : normalized.includes("warning") ||
          normalized.includes("approval") ||
          normalized.includes("executing") ||
          normalized.includes("blocked")
        ? "warning"
        : "info";
  return <Badge variant={variant}>{value}</Badge>;
}
