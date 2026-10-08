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
 * panel below says it, and the line says nothing.
 */
export function NodeHeader({
  kind,
  name,
  rag,
  reason,
  read,
  actions,
}: {
  kind: string;
  name: string;
  rag: Rag | null | undefined;
  reason?: string | null;
  read?: ReadState;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5">
      <p className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">{kind}</p>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h1 className="text-[30px] font-extrabold tracking-tight text-balance">{name}</h1>
        <RagBadge rag={rag} />
        {actions ? <div className="ml-auto flex flex-wrap gap-2">{actions}</div> : null}
      </div>
      {read?.error ? null : (
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
}: {
  factors: RollupFactorDto[];
  names: Record<string, string>;
}) {
  if (factors.length < 2) return null;
  const rows = reasonRows(factors, names);
  return (
    <Panel title="Every reason" note={reasonsNote(rows.length, factors.length)}>
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

/** Links to related nodes, each coloured by its own status. */
export function Related({
  label,
  kind,
  items,
}: {
  label: string;
  kind: string;
  items: (DirectoryItemResponse | undefined)[];
}) {
  const known = items.filter((x): x is DirectoryItemResponse => Boolean(x));
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
          <RagDot rag={item.rag} />
          {item.name}
        </Link>
      ))}
    </div>
  );
}
