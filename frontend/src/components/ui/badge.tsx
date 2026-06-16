import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

type BadgeTone = "neutral" | "success" | "warning";

const tones: Record<BadgeTone, string> = {
  neutral: "border-border bg-muted text-muted-foreground",
  success: "border-success/30 bg-success/10 text-success",
  warning: "border-warning/30 bg-warning/10 text-warning",
};

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center rounded border px-2 text-xs font-medium",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}
