import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { BriefKind } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { ChipPicker, Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { formatDate } from "../../lib/format";
import { plural, spaced } from "../../lib/words";

const BRIEF_FILTERS: { value: "all" | BriefKind; label: string }[] = [
  { value: "all", label: "All" },
  { value: "exec", label: "Exec" },
  { value: "weekly_project", label: "Weekly project" },
  { value: "daily_pod", label: "Daily pod" },
];

/** The newest few; real briefs are paragraphs, and a page of twenty buries the rest. */
const BRIEFS_SHOWN = 5;

/**
 * The briefs, newest first. Across All projects (no `scopeId`) they open on
 * the kind the role reads, `kind`: the daily pod brief for a scrum master, the
 * weekly project brief for a product owner, the exec brief for a manager,
 * executive or admin; "All" is a pick like the others (`?brief=all`), and a
 * link with no kind opens on the role's own. For one project or one pod
 * (`scopeId`) they are that one's briefs of `kind` only, with no picker.
 */
export function Briefs({
  kind: fallback,
  scopeId,
  title = "Briefs",
  empty,
}: {
  kind: BriefKind;
  /** One project's or one pod's briefs only: the kind is fixed, no picker. */
  scopeId?: string;
  title?: string;
  /** What an empty list says instead of the default. */
  empty?: string;
}) {
  const [search, setSearch] = useSearchParams();
  const [showAll, setShowAll] = useState(false);
  const raw = search.get("brief");
  const scoped = scopeId !== undefined;
  const kind: "all" | BriefKind =
    !scoped && BRIEF_FILTERS.some((f) => f.value === raw) ? (raw as "all" | BriefKind) : fallback;
  const briefs = useQuery({
    queryKey: ["briefs", kind],
    queryFn: () => apiClient.personaBriefs(kind === "all" ? undefined : kind, 20),
  });
  const all = (briefs.data?.briefs ?? []).filter((brief) => !scoped || brief.scope_id === scopeId);
  const shown = showAll ? all : all.slice(0, BRIEFS_SHOWN);

  return (
    <Panel title={title} note="written from facts only, no raw chat">
      <PanelState isLoading={briefs.isLoading} error={briefs.error}>
        {scoped ? null : (
          <ChipPicker
            label="Brief kind"
            value={kind}
            onChange={(next) => {
              setShowAll(false);
              setSearch(
                (current) => {
                  // Other parameters (the day being viewed) are the shell's; leave them.
                  const params = new URLSearchParams(current);
                  if (next === fallback) params.delete("brief");
                  else params.set("brief", next);
                  return params;
                },
                { replace: true },
              );
            }}
            options={BRIEF_FILTERS}
          />
        )}
        {all.length === 0 ? (
          <p className="text-[13px] text-grey-secondary">
            {empty ??
              "No briefs of this kind yet. They are written from the day's facts on a schedule."}
          </p>
        ) : (
          <>
            <ul className="grid gap-3 lg:grid-cols-2">
              {shown.map((b) => (
                <li
                  key={`${b.kind}-${b.scope_id}-${b.generated_at}`}
                  className="rounded-2xl border border-grey-border p-4"
                >
                  <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
                    {spaced(b.kind)} · {formatDate(b.generated_at)}
                  </p>
                  <p className="mt-1 text-[15px] font-extrabold">{b.title}</p>
                  {b.bullets && b.bullets.length > 0 ? (
                    <ul className="mt-2 grid list-disc gap-1 pl-5 text-[14px] text-grey-body">
                      {b.bullets.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1 text-[14px] text-grey-body">{b.body}</p>
                  )}
                  <p className="mt-2 text-[11px] text-grey-secondary">
                    From {plural(b.sources.length, "source", "sources")}
                  </p>
                </li>
              ))}
            </ul>
            {all.length > BRIEFS_SHOWN ? (
              <div className="mt-3">
                <Pill size="sm" variant="ghost" onClick={() => setShowAll((on) => !on)}>
                  {showAll ? "Show fewer" : `Show ${all.length - BRIEFS_SHOWN} older`}
                </Pill>
              </div>
            ) : null}
          </>
        )}
      </PanelState>
    </Panel>
  );
}
