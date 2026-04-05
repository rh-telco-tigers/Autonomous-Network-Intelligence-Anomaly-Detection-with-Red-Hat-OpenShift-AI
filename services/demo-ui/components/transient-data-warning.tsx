import * as React from "react";

import { Badge } from "@/components/ui/badge";

export function TransientDataWarning({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-[var(--warning-ring)] bg-[var(--warning-bg)] px-4 py-3 text-sm text-[var(--warning-fg)]">
      <Badge variant="warning" className="shrink-0">
        Stale data
      </Badge>
      <div>{children}</div>
    </div>
  );
}
