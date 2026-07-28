import { type ButtonHTMLAttributes, forwardRef } from "react";

import { cn } from "../../lib/utils";

type PillVariant = "primary" | "dark" | "ghost" | "success";
type PillSize = "sm" | "md" | "lg";

const sizeClasses: Record<PillSize, string> = {
  sm: "h-9 px-4 text-[13px]",
  md: "h-11 px-5 text-[15px]",
  lg: "h-12 px-6 text-[16px]",
};

const variantClasses: Record<PillVariant, string> = {
  primary:
    "bg-magenta text-white hover:bg-magenta-hover disabled:opacity-60 disabled:hover:bg-magenta",
  dark: "bg-ink text-white hover:bg-ink-hover",
  ghost: "bg-transparent text-ink border border-ink hover:bg-grey-fill",
  success: "bg-rag-green-bg text-rag-green cursor-default",
};

export const Pill = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: PillVariant;
  size?: PillSize;
}>(function Pill({ variant = "primary", size = "lg", className, children, ...rest }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-full font-bold transition-colors duration-150",
        sizeClasses[size],
        variantClasses[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
});
