import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useRef } from "react";

import type { NodeTrendResponse, Rag } from "../../api/schema";

// RAG is styled through the shared CSS tokens (index.css) rather than hardcoded
// hex, so the sparkline stays in sync with the rest of the design system.
const ragToken: Record<Rag, string> = {
  green: "--color-success",
  amber: "--color-warning",
  red: "--color-danger",
  unknown: "--color-muted-foreground",
};

const ragLabel: Record<Rag, string> = {
  green: "Green",
  amber: "Amber",
  red: "Red",
  unknown: "Unknown",
};

const FALLBACK_COLOR = "#94a3b8";

function resolveColor(element: HTMLElement, token: string): string {
  const value = getComputedStyle(element).getPropertyValue(token).trim();
  return value || FALLBACK_COLOR;
}

export function Sparkline({ data }: { data: NodeTrendResponse | undefined }) {
  const elementRef = useRef<HTMLDivElement | null>(null);
  const points = useMemo(() => data?.points ?? [], [data]);

  useEffect(() => {
    const element = elementRef.current;
    if (!element || points.length === 0) {
      return undefined;
    }
    // Resolved here (not in a render-time memo) so the CSS custom properties
    // are read from the mounted node, never from a not-yet-attached ref.
    const lineColor = resolveColor(element, "--color-muted-foreground");
    const option: EChartsOption = {
      animation: false,
      grid: { top: 8, right: 8, bottom: 8, left: 8 },
      xAxis: {
        type: "category",
        show: false,
        data: points.map((point) => point.as_of),
        boundaryGap: false,
      },
      yAxis: { type: "value", show: false, min: 0, max: 3 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const first = Array.isArray(params) ? params[0] : params;
          const index =
            typeof first === "object" && first !== null && "dataIndex" in first
              ? Number((first as { dataIndex: number }).dataIndex)
              : 0;
          const point = points[index];
          if (!point) {
            return "No status";
          }
          return `${point.as_of}<br/>RAG: ${ragLabel[point.rag]}<br/>Source: ${point.source}`;
        },
      },
      series: [
        {
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          data: points.map((point) => point.score),
          lineStyle: { color: lineColor, width: 2 },
          itemStyle: {
            color: (params: unknown) => {
              const index =
                typeof params === "object" && params !== null && "dataIndex" in params
                  ? Number((params as { dataIndex: number }).dataIndex)
                  : 0;
              const point = points[index];
              return resolveColor(element, ragToken[point?.rag ?? "unknown"]);
            },
          },
          areaStyle: { opacity: 0.08, color: lineColor },
        },
      ],
    };

    const chart = echarts.init(element);
    chart.setOption(option);
    const resizeObserver = new ResizeObserver(() => chart.resize());
    resizeObserver.observe(element);
    return () => {
      resizeObserver.disconnect();
      chart.dispose();
    };
  }, [points]);

  if (points.length === 0) {
    return (
      <div className="flex h-16 w-full items-center justify-center text-xs text-muted-foreground">
        No trend history yet
      </div>
    );
  }

  return <div ref={elementRef} className="h-16 w-full min-w-0" />;
}
