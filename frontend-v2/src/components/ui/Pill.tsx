import { type ButtonHTMLAttributes, forwardRef } from "react";

import { cn } from "../../lib/utils";

type PillVariant = "primary" | "dark" | "ghost" | "success";
type PillSize = "sm" | "md" | "lg";

const sizeClasses: Record<PillSize, string> = {
  sm: "h-9 px-4 text-[13px]",
  md: "h-11 px-5 text-[15px]",
  lg: "h-12 px-6 text-[16px]",
};

// Every variant has to look unavailable when it is, not just `primary`. A
// disabled ghost or dark pill was indistinguishable from an enabled one -- the
// Unlink buttons on the admin Links panel and the Ask button before a question
// is typed both invited a click that does nothing.
const DISABLED_CLASSES = "disabled:cursor-not-allowed disabled:opacity-50";

const variantClasses: Record<PillVariant, string> = {
  primary: "bg-magenta text-white hover:bg-magenta-hover disabled:hover:bg-magenta",
  dark: "bg-ink text-white hover:bg-ink-hover disabled:hover:bg-ink",
  ghost:
    "bg-transparent text-ink border border-ink hover:bg-grey-fill disabled:hover:bg-transparent",
  success: "bg-rag-green-bg text-rag-green cursor-default",
};

export const Pill = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: PillVariant;
    size?: PillSize;
  }
>(function Pill({ variant = "primary", size = "lg", className, children, ...rest }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-full font-bold transition-colors duration-150",
        DISABLED_CLASSES,
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
