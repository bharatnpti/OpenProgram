import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "../../lib/utils";

export function Card({
  variant = "white",
  padding = "p-6",
  animateDelay,
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement> & {
  variant?: "white" | "grey";
  padding?: string;
  animateDelay?: number;
  children: ReactNode;
}) {
  return (
    <section
      className={cn(
        "rounded-3xl",
        variant === "white" ? "border border-grey-border bg-white" : "bg-grey-fill",
        padding,
        "animate-op-fade-up",
        className,
      )}
      style={animateDelay ? { animationDelay: `${animateDelay}ms` } : undefined}
      {...rest}
    >
      {children}
    </section>
  );
}
