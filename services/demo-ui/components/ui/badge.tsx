import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium", {
  variants: {
    variant: {
      default: "bg-[var(--surface-subtle)] text-[var(--text-secondary)] ring-1 ring-[var(--border-subtle)]",
      success: "bg-[var(--success-bg)] text-[var(--success-fg)] ring-1 ring-[var(--success-ring)]",
      warning: "bg-[var(--warning-bg)] text-[var(--warning-fg)] ring-1 ring-[var(--warning-ring)]",
      danger: "bg-[var(--danger-bg)] text-[var(--danger-fg)] ring-1 ring-[var(--danger-ring)]",
      info: "bg-[var(--info-bg)] text-[var(--info-fg)] ring-1 ring-[var(--info-ring)]",
    },
  },
  defaultVariants: {
    variant: "default",
  },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
