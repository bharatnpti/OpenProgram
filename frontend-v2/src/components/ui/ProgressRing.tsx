import type { CSSProperties } from "react";

const CIRCUMFERENCE = 264;

export function ProgressRing({
  percent,
  color,
  size = 120,
  stroke = 9,
  label,
}: {
  percent: number;
  color: string;
  size?: number;
  stroke?: number;
  label?: string;
}) {
  const clamped = Math.max(0, Math.min(100, percent));
  const radius = CIRCUMFERENCE / (2 * Math.PI);
  const offset = CIRCUMFERENCE * (1 - clamped / 100);

  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 100 100" className="-rotate-90">
        <circle
          cx="50"
          cy="50"
          r={radius}
          fill="none"
          stroke="var(--op-grey-border)"
          strokeWidth={stroke}
        />
        <circle
          cx="50"
          cy="50"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          className="animate-op-ring"
          style={{ "--op-ring-offset": offset } as CSSProperties}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <span className="tabular-nums text-[26px] font-extrabold">
          {label ?? `${Math.round(clamped)}%`}
        </span>
      </div>
    </div>
  );
}
