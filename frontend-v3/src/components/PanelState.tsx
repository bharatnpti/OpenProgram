import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import { useRole } from "../app/role";
import { Pill } from "./ui/Pill";

/**
 * The one way a panel shows that it is loading, refused, empty or broken.
 *
 * A refused read says which role opens it instead of rendering dashes or a 0%
 * bar, and nothing that failed or is still loading is ever drawn as on track.
 */
export function PanelState({
  locked,
  needs,
  isLoading,
  error,
  onRetry,
  isEmpty,
  emptyText,
  children,
}: {
  /** The viewing role cannot read this; the query was not sent. */
  locked?: boolean;
  /** Who opens it, in words: "a product owner, manager, executive or admin". */
  needs: string;
  isLoading: boolean;
  error: unknown;
  onRetry?: () => void;
  isEmpty?: boolean;
  emptyText?: ReactNode;
  children: ReactNode;
}) {
  const { roleLabel } = useRole();

  if (locked) {
    return (
      <Notice>
        Opens for {needs}. You are viewing as {roleLabel.toLowerCase()}.
      </Notice>
    );
  }
  if (isLoading) {
    return <Notice>Loading…</Notice>;
  }
  if (error) {
    if (error instanceof ApiError && error.status === 403) {
      return (
        <Notice>
          Opens for {needs}.{" "}
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
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-[22px] font-extrabold tracking-tight text-balance">{title}</h2>
        {meta ? <p className="mt-1 text-[13px] text-grey-secondary">{meta}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

/** Wide tables scroll inside their own box, never the page. */
export function TableBox({ children }: { children: ReactNode }) {
  return <div className="overflow-x-auto rounded-2xl border border-grey-border">{children}</div>;
}

export const th =
  "border-b border-grey-border bg-grey-header px-3 py-2 text-left text-[11px] font-bold uppercase tracking-wider text-grey-secondary whitespace-nowrap";
export const td = "border-b border-grey-border px-3 py-2.5 align-top text-[13px]";
