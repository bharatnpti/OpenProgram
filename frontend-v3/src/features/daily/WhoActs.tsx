import { type OwnerAsks, NEED_WORDS, whoActs } from "./dailyViz";
import { EscalationFlag } from "./vizBits";
import { NEED_COLOUR } from "./vizColours";

/**
 * D3, who acts, under What we need: who has to do something today, and how long
 * has it waited? A list, one row per person in the report's order; each ask has its
 * kind, a bar for the days it has waited, "Needed most" where In short names it, and
 * a flag where it escalated, with the one "Escalated to" line above the lanes. It
 * replaces the per-person ask list, "Needed most" and the repeated "Escalated to"
 * clauses. The report never ranks people: the order is the report's, by the work.
 */
export function WhoActs({ owners, emptyText }: { owners: OwnerAsks[]; emptyText: string }) {
  if (!owners.length) {
    return <p className="text-[14px] text-(--op-day-secondary)">{emptyText}</p>;
  }
  const view = whoActs(owners);

  return (
    <figure className="dv-viz" aria-label="Who needs to act">
      {view.escalated ? (
        <p className="mb-1 flex items-start gap-2 text-[13px] text-(--op-day-body)">
          <EscalationFlag className="mt-0.5" />
          <span className="min-w-0">{view.escalated}</span>
        </p>
      ) : null}
      {/* The bars' scale: numbers on the track, the unit once over the day counts. On a
          phone only the scale shows, above the lanes, at the bars' own width. */}
      <div className="dv-lane dv-lane-scale" aria-hidden>
        <span className="dv-scale-head text-[11px] font-bold uppercase tracking-wider text-(--op-day-secondary)">
          Who
        </span>
        <div className="dv-scale-row">
          <span className="dv-scale-head text-[11px] font-bold uppercase tracking-wider text-(--op-day-secondary)">
            Asked
          </span>
          <div className="dv-wait">
            <div className="dv-scale">
              {view.scale.ticks.map((tick) => (
                <span key={tick.label} style={{ left: `${tick.at}%` }}>
                  {tick.label}
                </span>
              ))}
            </div>
            <span className="dv-scale-unit">{view.scale.unit}</span>
            <span />
          </div>
        </div>
      </div>
      <ul aria-label="What we need, one row per person, in the report's order">
        {view.lanes.map((lane) => (
          <li key={lane.heading} className="dv-lane">
            <p
              className={
                lane.named
                  ? "min-w-0 text-[13.5px] font-extrabold"
                  : "min-w-0 text-[13.5px] font-extrabold text-(--op-day-secondary)"
              }
            >
              {lane.heading}
            </p>
            <ul className="grid gap-2">
              {lane.asks.map((ask, index) => (
                <li key={`${ask.text}-${index}`} className="dv-ask">
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px] text-(--op-day-body)">
                    <span className="inline-flex items-center gap-[5px] text-[11px] font-extrabold uppercase tracking-wide text-(--op-day-secondary)">
                      <i
                        className="inline-block h-2.5 w-2.5 rounded-[3px]"
                        style={{ background: NEED_COLOUR[ask.need] }}
                        aria-hidden
                      />
                      {ask.kind}
                    </span>
                    <span className="min-w-0">{ask.text}</span>
                    {ask.neededMost ? (
                      <span className="inline-flex h-5 items-center rounded-full bg-(--op-day-ink) px-[7px] text-[11px] font-bold text-(--op-day-surface)">
                        Needed most
                      </span>
                    ) : null}
                  </p>
                  <div className="dv-wait" title={ask.waited}>
                    <span className="dv-track">
                      <span
                        className="block h-full rounded-r"
                        style={{ width: `${ask.width}%`, background: NEED_COLOUR[ask.need] }}
                      />
                    </span>
                    <span className="text-right text-[12px] font-extrabold tabular-nums">
                      <span aria-hidden>{ask.daysText}</span>
                      <span className="sr-only">{ask.waited}</span>
                    </span>
                    {ask.escalation ? (
                      <span className="inline-flex" title={ask.escalation}>
                        <EscalationFlag />
                        <span className="sr-only">{ask.escalation}</span>
                      </span>
                    ) : (
                      <span />
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <div className="dv-legend">
        {view.kinds.map((need) => (
          <span key={need}>
            <i className="dv-sw" style={{ background: NEED_COLOUR[need] }} />
            {NEED_WORDS[need]}
          </span>
        ))}
        {view.anyEscalated ? (
          <span className="font-bold text-(--op-day-red)">
            <EscalationFlag />
            Escalated
          </span>
        ) : null}
        <span className="text-(--op-day-secondary)">Bar length is days waiting</span>
      </div>
    </figure>
  );
}
