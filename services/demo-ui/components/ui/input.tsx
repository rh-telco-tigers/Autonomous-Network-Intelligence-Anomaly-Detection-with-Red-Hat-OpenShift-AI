import * as React from "react";

import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => {
    return (
      <input
        ref={ref}
        className={cn(
          "flex h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-strong)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none ring-offset-[var(--app-body-bg)] placeholder:text-[var(--text-subtle)] focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]",
          className,
        )}
        {...props}
      />
    );
  },
);

Input.displayName = "Input";
