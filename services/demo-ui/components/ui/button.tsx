import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center rounded-xl text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)] disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-[var(--accent-strong)] text-[var(--accent-contrast)] hover:opacity-95",
        secondary:
          "bg-[var(--surface-raised)] text-[var(--text-primary)] ring-1 ring-[var(--border-subtle)] hover:bg-[var(--surface-hover)]",
        outline:
          "bg-transparent text-[var(--text-primary)] ring-1 ring-[var(--border-subtle)] hover:bg-[var(--surface-subtle)]",
        ghost: "bg-transparent text-[var(--text-secondary)] hover:bg-[var(--surface-subtle)]",
        danger:
          "bg-[var(--danger-bg)] text-[var(--danger-fg)] ring-1 ring-[var(--danger-ring)] hover:bg-[var(--danger-bg-hover)]",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 px-3",
        lg: "h-11 px-5",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);

Button.displayName = "Button";
