import { Flag } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/utils";
import type { Tone } from "./dailyViz";
import { TONE_CHIP } from "./vizColours";

/* Small pieces Daily's pictures share. */

/** A chip in the report's own colours, which follow the document's theme. */
export function DayChip({
  tone,
  dot = false,
  small = false,
  children,
}: {
  tone: Tone | "info";
  dot?: boolean;
  small?: boolean;
  children: ReactNode;
}) {
  const colours = TONE_CHIP[tone];
  return (
    <span className={cn("dv-chip", small && "dv-chip-sm")} style={colours}>
      {dot ? <span className="dv-dot" style={{ background: colours.color }} /> : null}
      {children}
    </span>
  );
}

/** The escalation flag: red, and never without its words beside it or for a reader. */
export function EscalationFlag({ className }: { className?: string }) {
  return (
    <Flag
      aria-hidden
      className={cn("h-3.5 w-3.5 flex-none", className)}
      style={{ color: "var(--op-day-red)", fill: "currentColor" }}
    />
  );
}
