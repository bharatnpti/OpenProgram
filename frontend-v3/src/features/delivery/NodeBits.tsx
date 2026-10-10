import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { DirectoryItemResponse, Rag, RollupFactorDto } from "../../api/schema";
import { Panel, RagBadge, RagDot, Row } from "../../components/ui/Bits";
import type { ReadState } from "../../lib/readState";
import { cn } from "../../lib/utils";
import { reasonsNote, reasonRows, sourcesLine } from "./factors";

/**
 * The top of every Delivery panel: kind, name, colour, and the one-line reason.
 * `read` is the read the reason comes from: "No reason recorded." is a finding,
 * said once that read has answered. Loading, the line says so; failed, the
 * panel below says it, and the line says nothing. `reasons: false` for a reader
 * who reads the colour but not why (a pod's dates only), so no line claims none
 * was recorded; `status: false` for one who reads neither (a developer's
 * project), so no chip says "unknown" about what is merely not theirs to read.
 */
export function NodeHeader({
  kind,
  name,
  rag,
  reason,
  read,
  actions,
  above,
  status = true,
  reasons = status,
}: {
  kind: string;
  name: string;
  rag: Rag | null | undefined;
  reason?: string | null;
  read?: ReadState;
  actions?: ReactNode;
  /** A line above the kind, such as the trail of where the node sits. */
  above?: ReactNode;
  status?: boolean;
  reasons?: boolean;
}) {
  return (
    <div className="mb-5">
      {above}
      <p className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">{kind}</p>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h1 className="text-[30px] font-extrabold tracking-tight text-balance">{name}</h1>
        {status ? <RagBadge rag={rag} /> : null}
        {actions ? <div className="ml-auto flex flex-wrap gap-2">{actions}</div> : null}
      </div>
      {read?.error || !reasons ? null : (
        <p className="mt-1 text-[15px] text-grey-body">
          {read?.isLoading
            ? "Loading…"
            : (reason ??
              (rag && rag !== "unknown" ? "No reason recorded." : "Nothing recorded for it yet."))}
        </p>
      )}
    </div>
  );
}

/**
 * Every reason behind a colour, worst first. The same reason from several
 * places is one row that names them, and a source the server names is shown
 * by name, else by kind and id (never a guessed name).
 */
export function FactorsPanel({
  factors,
  names,
  title = "Every reason",
  always = false,
  footer,
}: {
  factors: RollupFactorDto[];
  names: Record<string, string>;
  title?: string;
  /**
   * Shown with a single reason too. A Delivery panel's header already says its
   * one reason, so there it waits for a second; a Today has no such header.
   */
  always?: boolean;
  footer?: ReactNode;
}) {
  if (factors.length < (always ? 1 : 2)) return null;
  const rows = reasonRows(factors, names);
  return (
    <Panel title={title} note={reasonsNote(rows.length, factors.length)}>
      <ul>
        {rows.map((row) => (
          <Row
            key={row.key}
            rag={row.contributes}
            title={row.description}
            meta={`${row.kind} · ${sourcesLine(row.sources)}`}
          />
        ))}
      </ul>
      {footer}
    </Panel>
  );
}

/** A node's facts (its metadata and the people it names), as small labelled tiles. */
export function Facts({ facts }: { facts: [string, string][] }) {
  if (facts.length === 0) return null;
  return (
    <dl className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {facts.map(([label, value]) => (
        <div
          key={label}
          className={cn(
            "min-w-0 rounded-2xl bg-grey-fill px-3 py-2",
            label === "Repositories" && "col-span-2",
          )}
        >
          <dt className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
            {label}
          </dt>
          <dd className="mt-0.5 break-words text-[14px] font-bold">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

type LinkedNode = Pick<DirectoryItemResponse, "id" | "name" | "rag"> & {
  /** The reader reads no colour for it: no dot, rather than a grey one. */
  noStatus?: boolean;
};

/** A panel opened from the person's own part of the tree (`GET /me/delivery-tree`). */
export type OwnPanel = {
  /** Its whole panel is theirs: a project's progress and dates, a pod's detail and dates. */
  full: boolean;
  /** The related nodes to link: the listed ones that open, coloured as the navigator shows. */
  related: LinkedNode[];
  /** Where it sits, above its heading. */
  trail?: ReactNode;
};

/** The project's two reports, which every role has. */
export function ReportLinks({ projectId }: { projectId: string }) {
  const id = encodeURIComponent(projectId);
  return (
    <>
      <Link
        to={`/reports/${id}/daily`}
        className="inline-flex h-9 items-center rounded-full bg-ink px-4 text-[13px] font-bold text-white no-underline"
      >
        Daily report
      </Link>
      <Link
        to={`/reports/${id}/overall`}
        className="inline-flex h-9 items-center rounded-full border border-ink px-4 text-[13px] font-bold text-ink no-underline"
      >
        Overall
      </Link>
    </>
  );
}

/**
 * Where a node sits, above its heading: "Digital Platform Program › Checkout
 * Revamp". A program is named only; a project the person opens is a link.
 */
export function Trail({ steps }: { steps: { label: string; to: string | null }[] }) {
  if (steps.length === 0) return null;
  return (
    <nav aria-label="Where this sits" className="mb-2 text-[13px] text-grey-secondary">
      <ol className="flex flex-wrap items-center gap-x-1.5">
        {steps.map((step, index) => (
          <li key={`${index}:${step.label}`} className="flex items-center gap-x-1.5">
            {index > 0 ? <span aria-hidden>›</span> : null}
            {step.to ? (
              <Link to={step.to} className="text-grey-body underline-offset-2 hover:underline">
                {step.label}
              </Link>
            ) : (
              <span>{step.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** Links to related nodes, each coloured by its own status. */
export function Related({
  label,
  kind,
  items,
}: {
  label: string;
  kind: string;
  items: (LinkedNode | undefined)[];
}) {
  const known = items.filter((x): x is LinkedNode => Boolean(x));
  if (known.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
        {label}
      </span>
      {known.map((item) => (
        <Link
          key={item.id}
          to={`/delivery/${kind}/${item.id}`}
          className="inline-flex items-center gap-2 rounded-full border border-grey-border px-3 py-1 text-[13px] font-bold text-ink no-underline hover:bg-grey-fill"
        >
          {item.noStatus ? null : <RagDot rag={item.rag} />}
          {item.name}
        </Link>
      ))}
    </div>
  );
}
