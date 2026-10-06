import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { DirectoryItemResponse, Rag, RollupFactorDto } from "../../api/schema";
import { Panel, RagBadge, RagDot, Row } from "../../components/ui/Bits";
import { worstFirst } from "./factors";

/** The top of every Delivery panel: kind, name, colour, and the one-line reason. */
export function NodeHeader({
  kind,
  name,
  rag,
  reason,
  actions,
}: {
  kind: string;
  name: string;
  rag: Rag | null | undefined;
  reason?: string | null;
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
      <p className="mt-1 text-[15px] text-grey-body">
        {reason ??
          (rag && rag !== "unknown" ? "No reason recorded." : "Nothing recorded for it yet.")}
      </p>
    </div>
  );
}

export function FactorsPanel({
  factors,
  names,
}: {
  factors: RollupFactorDto[];
  names: Record<string, string>;
}) {
  if (factors.length < 2) return null;
  return (
    <Panel title="Every reason" note={`${factors.length} factors`}>
      <ul>
        {worstFirst(factors).map((f, i) => (
          <Row
            key={`${f.kind}-${i}`}
            rag={f.contributes}
            title={f.description}
            meta={`${f.kind.replace(/_/g, " ")}${names[f.source_ref.id] ? ` · ${names[f.source_ref.id]}` : ` · ${f.source_ref.kind} ${f.source_ref.id}`}`}
          />
        ))}
      </ul>
    </Panel>
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
