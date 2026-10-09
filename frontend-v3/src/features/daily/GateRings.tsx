import { type GateFacts, gateRing } from "./dailyViz";
import { TONE_STROKE } from "./vizColours";

/**
 * D4, the gate rings, under Where we stand: are requirements passing their gates,
 * or going around them? One ring per gate, each requirement counted once, with a
 * count and a word for every colour. They replace the two "Acceptance and tests"
 * lines. With no gate switched on the report says nothing here, so neither does this.
 */
export function GateRings({ gates }: { gates: GateFacts[] }) {
  if (!gates.length) return null;
  return (
    <figure className="dv-viz" aria-label="Acceptance and tests">
      <p className="dv-grp">Acceptance and tests</p>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,250px),1fr))] gap-x-7 gap-y-4">
        {gates.map((gate) => (
          <GateRing key={gate.name} gate={gate} />
        ))}
      </div>
    </figure>
  );
}

function GateRing({ gate }: { gate: GateFacts }) {
  const view = gateRing(gate);
  return (
    <div>
      <p className="mb-2 text-[13px] font-extrabold">
        {view.name} <span className="font-medium text-(--op-day-secondary)">· {view.before}</span>
      </p>
      <div className="flex items-center gap-3.5">
        <svg className="h-24 w-24 flex-none" viewBox="0 0 96 96" role="img" aria-label={view.label}>
          <circle
            cx={48}
            cy={48}
            r={39}
            fill="none"
            stroke="var(--op-day-surface-2)"
            strokeWidth={11}
          />
          {view.segments.map((segment) => (
            <circle
              key={segment.title}
              cx={48}
              cy={48}
              r={39}
              fill="none"
              stroke={TONE_STROKE[segment.tone]}
              strokeWidth={11}
              strokeDasharray={segment.dash}
              strokeDashoffset={segment.offset}
              transform="rotate(-90 48 48)"
            >
              <title>{segment.title}</title>
            </circle>
          ))}
          <text
            x={48}
            y={52}
            className="dv-tx dv-tx-ink dv-xb dv-num"
            style={{ fontSize: 22 }}
            textAnchor="middle"
          >
            {view.passed}
          </text>
          <text x={48} y={67} className="dv-tx dv-s" textAnchor="middle">
            of {view.total}
          </text>
        </svg>
        <ul className="grid min-w-0 gap-1.5 text-[12.5px] text-(--op-day-body)">
          {view.rows.map((row) => (
            <li key={row.words} className="flex items-baseline gap-[7px]">
              <i
                className="inline-block h-2 w-2 flex-none -translate-y-px rounded-full"
                style={{ background: TONE_STROKE[row.tone] }}
                aria-hidden
              />
              <span>
                <b className="tabular-nums">{row.count}</b> {row.words}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
