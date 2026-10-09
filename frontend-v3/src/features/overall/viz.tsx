import { useState, type CSSProperties, type ReactNode } from "react";

import type { Rag } from "../../api/schema";
import { cn } from "../../lib/utils";

/*
 * The pieces Overall's visuals share: the card they sit in, a fold that draws
 * its contents only once opened, and the swatches and line keys of a legend.
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

/**
 * A native disclosure whose contents render only once it is first opened, so
 * a folded table of every requirement costs nothing until someone asks for it.
 */
export function Fold({
  summary,
  children,
  className,
}: {
  summary: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const [opened, setOpened] = useState(false);
  return (
    <details
      className={cn("group", className)}
      onToggle={(event) => {
        if ((event.currentTarget as HTMLDetailsElement).open) setOpened(true);
      }}
    >
      <summary className="inline-flex min-h-9 cursor-pointer list-none items-center gap-2 rounded-full border border-grey-border px-3.5 text-[13px] font-bold max-sm:min-h-11 [&::-webkit-details-marker]:hidden">
        <span
          aria-hidden
          className="inline-grid h-[18px] w-[18px] place-items-center rounded-full bg-grey-fill font-extrabold leading-none"
        >
          <span className="group-open:hidden">+</span>
          <span className="hidden group-open:inline">−</span>
        </span>
        {summary}
      </summary>
      {opened ? <div className="mt-3">{children}</div> : null}
    </details>
  );
}

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
