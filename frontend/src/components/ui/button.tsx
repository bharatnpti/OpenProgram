import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/utils";

type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "icon";

const variants: Record<ButtonVariant, string> = {
  primary:
    "border-primary bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80",
  secondary: "border-border bg-surface text-foreground hover:bg-surface-muted active:bg-muted",
  outline: "border-border bg-transparent text-foreground hover:bg-surface-muted active:bg-muted",
  ghost:
    "border-transparent bg-transparent text-muted-foreground shadow-none hover:bg-surface-muted hover:text-foreground",
  danger: "border-danger bg-danger text-white hover:bg-danger/90 active:bg-danger/80",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-2.5 text-xs",
  md: "h-9 px-3 text-sm",
  icon: "h-9 w-9 justify-center px-0",
};

export function Button({
  className,
  variant = "secondary",
  size = "md",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  return (
    <button
      className={cn(
        "inline-flex shrink-0 cursor-pointer items-center gap-2 rounded-md border font-medium shadow-sm transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-60",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
}
