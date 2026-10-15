import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

/**
 * A titled card on the portfolio pages, on the viz surface token so it reads as
 * one card in the dark theme too (the page sits in `.op-viz`, which remaps the
 * greys, ink and RAG tokens; a plain white card would stay white).
 */
export function VizPanel({
  title,
  note,
  children,
  className,
  titleId,
}: {
  title: ReactNode;
  note?: ReactNode;
  children: ReactNode;
  className?: string;
  titleId?: string;
}) {
  return (
    <section
      aria-labelledby={titleId}
      className={cn(
        "min-w-0 rounded-3xl border border-grey-border bg-(--op-viz-surface) p-5 max-sm:rounded-[20px] max-sm:p-4",
        className,
      )}
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={titleId} className="text-[17px] font-extrabold">
          {title}
        </h3>
        {note ? <span className="text-[12px] text-grey-secondary">{note}</span> : null}
      </div>
      {children}
    </section>
  );
}
