import type { Verdict } from "../../api/schema";
import type { BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import { verdictTone, verdictWords } from "./hubTabs";

const DOT: Record<BadgeTone, string> = {
  success: "bg-rag-green",
  warning: "bg-rag-amber",
  danger: "bg-rag-red",
  info: "bg-rag-info",
  neutral: "bg-rag-unknown",
};

/**
 * A project's or a pod's date verdict as a dot, its words on hover and, for a
 * screen reader, after the name (`VerdictWords`). `none` draws an empty ring:
 * the reader reads no date for it.
 */
export function VerdictDot({
  verdict,
  none = false,
  className,
}: {
  verdict: Verdict | null;
  none?: boolean;
  className?: string;
}) {
  if (none) {
    return <span aria-hidden className={cn("inline-block h-2.5 w-2.5 flex-none", className)} />;
  }
  return (
    <span
      aria-hidden
      title={verdictWords(verdict)}
      className={cn(
        "inline-block h-2.5 w-2.5 flex-none rounded-full",
        DOT[verdictTone(verdict)],
        className,
      )}
    />
  );
}
