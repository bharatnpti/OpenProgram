import { useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError } from "../api/client";
import { Pill } from "./ui/Pill";

/**
 * The one way a panel shows that it is loading, refused, empty or broken.
 *
 * A panel the viewing role is not offered is not drawn at all (app/access.ts),
 * so this never says which role would open it. A read the server refuses anyway
 * says so, with the server's reason, instead of rendering dashes or a 0% bar;
 * nothing that failed or is still loading is ever drawn as on track.
 */
export function PanelState({
  isLoading,
  error,
  onRetry,
  isEmpty,
  emptyText,
  children,
}: {
  /**
   * The read has not answered. Empty text is drawn only when this is false, so a
   * read that is held back (`enabled: false`) until another answers must count as
   * loading here: `read.isLoading` is false for it. Spread `readState(read, other)`
   * (lib/readState.ts) for `isLoading` and `error` together.
   */
  isLoading: boolean;
  error: unknown;
  onRetry?: () => void;
  isEmpty?: boolean;
  emptyText?: ReactNode;
  children: ReactNode;
}) {
  if (isLoading) {
    return <Notice>Loading…</Notice>;
  }
  if (error) {
    if (error instanceof ApiError && error.status === 403) {
      return (
        <Notice>
          Not available to you.{" "}
          <span className="text-grey-secondary">The server said: {error.message}</span>
        </Notice>
      );
    }
    const message = error instanceof Error ? error.message : "Something went wrong.";
    return (
      <Notice tone="error">
        <span>Could not load this: {message}</span>
        {onRetry ? (
          <Pill variant="ghost" size="sm" onClick={onRetry}>
            Try again
          </Pill>
        ) : null}
      </Notice>
    );
  }
  if (isEmpty) {
    return <Notice>{emptyText ?? "Nothing recorded yet."}</Notice>;
  }
  return <>{children}</>;
}

function Notice({ children, tone }: { children: ReactNode; tone?: "error" }) {
  return (
    <div
      className={
        tone === "error"
          ? "flex flex-wrap items-center gap-3 rounded-2xl bg-rag-red-bg px-4 py-3 text-[14px] text-rag-red"
          : "rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body"
      }
    >
      {children}
    </div>
  );
}

export function SectionHeader({
  title,
  meta,
  actions,
  id,
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  /** The heading's id, for a section labelled by it. */
  id?: string;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 id={id} className="text-[22px] font-extrabold tracking-tight text-balance">
          {title}
        </h2>
        {meta ? <p className="mt-1 text-[13px] text-grey-secondary">{meta}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

/**
 * Wide tables scroll inside their own box, never the page. A table wider than
 * its box says so: a shadow on the edge that has more beyond it, and a line
 * under it, because a column cut off at the edge of a phone looks like a
 * table of one letter. The box can be scrolled from the keyboard too.
 */
export function TableBox({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState({ overflows: false, left: false, right: false });

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const measure = () => {
      const overflows = element.scrollWidth > element.clientWidth + 1;
      const next = {
        overflows,
        left: overflows && element.scrollLeft > 1,
        right: overflows && element.scrollLeft + element.clientWidth < element.scrollWidth - 1,
      };
      setScroll((current) =>
        current.overflows === next.overflows &&
        current.left === next.left &&
        current.right === next.right
          ? current
          : next,
      );
    };
    measure();
    element.addEventListener("scroll", measure, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    if (element.firstElementChild) observer?.observe(element.firstElementChild);
    return () => {
      element.removeEventListener("scroll", measure);
      observer?.disconnect();
    };
  }, []);

  const shadow = (side: "left" | "right") => ({
    backgroundImage: `linear-gradient(to ${side === "left" ? "right" : "left"}, rgba(0,0,0,0.12), rgba(0,0,0,0))`,
  });
  return (
    <div>
      <div className="relative">
        <div
          ref={box}
          // Only a box that scrolls is a stop for the keyboard, with a name to say what it is.
          tabIndex={scroll.overflows ? 0 : undefined}
          role={scroll.overflows ? "region" : undefined}
          aria-label={scroll.overflows ? "Table, scrolls sideways" : undefined}
          className="overflow-x-auto rounded-2xl border border-grey-border"
        >
          {children}
        </div>
        {scroll.left ? (
          <span
            aria-hidden
            className="pointer-events-none absolute inset-y-px left-px w-4 rounded-l-2xl"
            style={shadow("left")}
          />
        ) : null}
        {scroll.right ? (
          <span
            aria-hidden
            className="pointer-events-none absolute inset-y-px right-px w-4 rounded-r-2xl"
            style={shadow("right")}
          />
        ) : null}
      </div>
      {scroll.overflows ? (
        <p className="mt-1.5 text-[11px] text-grey-secondary">
          This table is wider than the screen: scroll sideways for the other columns.
        </p>
      ) : null}
    </div>
  );
}

export const th =
  "border-b border-grey-border bg-grey-header px-3 py-2 text-left text-[11px] font-bold uppercase tracking-wider text-grey-secondary whitespace-nowrap";
export const td = "border-b border-grey-border px-3 py-2.5 align-top text-[13px]";
