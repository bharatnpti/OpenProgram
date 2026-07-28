export function SegmentedBar({
  segments,
  height = 14,
}: {
  segments: { value: number; color: string; label?: string }[];
  height?: number;
}) {
  const total = segments.reduce((sum, s) => sum + s.value, 0) || 1;

  return (
    <div
      className="flex w-full overflow-hidden rounded-full bg-grey-border"
      style={{ height }}
    >
      {segments
        .filter((s) => s.value > 0)
        .map((segment, index) => (
          <div
            key={index}
            title={segment.label}
            className="animate-op-bar h-full"
            style={{
              width: `${(segment.value / total) * 100}%`,
              backgroundColor: segment.color,
              animationDelay: `${index * 110}ms`,
            }}
          />
        ))}
    </div>
  );
}
