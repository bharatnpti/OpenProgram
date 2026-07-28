import { toneForRag, toneHex } from "../../lib/status";
import type { Rag } from "../../api/schema";

export function Sparkline({
  points,
  width = 320,
  height = 72,
  stroke = "var(--op-amber)",
}: {
  points: { score: number; rag: Rag }[];
  width?: number;
  height?: number;
  stroke?: string;
}) {
  if (points.length < 2) {
    return <div style={{ width, height }} />;
  }
  const scores = points.map((p) => p.score);
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const range = max - min || 1;
  const stepX = width / (points.length - 1);
  const coords = points.map((p, i) => ({
    x: i * stepX,
    y: height - ((p.score - min) / range) * height,
    rag: p.rag,
  }));
  const path = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={600}
        strokeDasharray={600}
        className="animate-op-draw"
      />
      {coords.map((c, i) => (
        <circle
          key={i}
          cx={c.x}
          cy={c.y}
          r={3.5}
          fill={toneHex[toneForRag(c.rag)]}
        />
      ))}
    </svg>
  );
}
