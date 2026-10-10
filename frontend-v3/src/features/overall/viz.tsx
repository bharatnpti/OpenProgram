import type { CSSProperties, ReactNode } from "react";

import type { Rag } from "../../api/schema";
import { cn } from "../../lib/utils";

/*
 * The pieces Overall's visuals share: the card they sit in, and the swatches and
 * line keys of a legend. The fold is components/viz/Fold.tsx, Daily's too.
 * Every colour comes from a token in index.css, so the dark theme hook (and the
 * .op-viz remap on the Overall page) reaches them: inline styles read the
 * --op-* tokens, utilities the theme's --color-* names.
 */

/** A visual's card: the console's card, on the viz surface token so it turns with the theme. */
export function VizCard({
  children,
  className,
  edge,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  /** A coloured left edge, the verdict's: the answer card's. */
  edge?: Rag;
  "aria-label"?: string;
}) {
  return (
    <div
      className={cn(
        "grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 rounded-3xl border border-grey-border bg-(--op-viz-surface) p-5 max-sm:rounded-[20px] max-sm:p-4",
        edge && "border-l-[6px]",
        edge && EDGE[edge],
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

const EDGE: Record<Rag, string> = {
  red: "border-l-rag-red",
  amber: "border-l-rag-amber",
  green: "border-l-rag-green",
  unknown: "border-l-grey-border",
};

/** A legend's square swatch. */
export function Swatch({ style, className }: { style?: CSSProperties; className?: string }) {
  return (
    <i
      aria-hidden
      className={cn("inline-block h-2.5 w-2.5 flex-none rounded-[3px]", className)}
      style={style}
    />
  );
}

/** A legend's line key: solid for a date, dashed for none yet. */
export function LineKey({ dashed = false }: { dashed?: boolean }) {
  return (
    <i
      aria-hidden
      className={cn(
        "inline-block h-0 w-[18px] flex-none border-t-2",
        dashed ? "border-dashed border-grey-secondary" : "border-ink",
      )}
    />
  );
}

/** The legend under a visual: each colour with its word. */
export function Legend({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-grey-body [&>span]:inline-flex [&>span]:min-w-0 [&>span]:items-center [&>span]:gap-1.5">
      {children}
    </div>
  );
}
