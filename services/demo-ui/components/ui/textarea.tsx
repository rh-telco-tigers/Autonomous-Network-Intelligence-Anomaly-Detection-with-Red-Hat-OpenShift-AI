import * as React from "react";

import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        className={cn(
          "min-h-[112px] w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-strong)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none ring-offset-[var(--app-body-bg)] placeholder:text-[var(--text-subtle)] focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]",
          className,
        )}
        {...props}
      />
    );
  },
);

Textarea.displayName = "Textarea";
