import type { ReactNode } from "react";

import { cn } from "../../lib/utils";
import type { BadgeTone } from "../../lib/status";

const toneClasses: Record<BadgeTone, string> = {
  success: "bg-rag-green-bg text-rag-green",
  warning: "bg-rag-amber-bg text-rag-amber",
  danger: "bg-rag-red-bg text-rag-red",
  info: "bg-rag-info-bg text-rag-info",
  neutral: "bg-rag-unknown-bg text-rag-unknown",
};

const dotClasses: Record<BadgeTone, string> = {
  success: "bg-rag-green",
  warning: "bg-rag-amber",
  danger: "bg-rag-red",
  info: "bg-rag-info",
  neutral: "bg-rag-unknown",
};

export function RagChip({
  tone,
  children,
  dot = false,
  pulse = false,
  className,
}: {
  tone: BadgeTone;
  children: ReactNode;
  dot?: boolean;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-7 items-center gap-2 rounded-full px-3.5 text-[13px] font-bold",
        toneClasses[tone],
        className,
      )}
    >
      {dot ? (
        <span
          className={cn(
            "inline-block h-2 w-2 rounded-full",
            dotClasses[tone],
            pulse && "animate-op-pulse",
          )}
        />
      ) : null}
      {children}
    </span>
  );
}
