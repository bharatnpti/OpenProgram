import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useRef } from "react";

import type { PortfolioHeatmapResponse, Rag } from "../../api/schema";

const ragScores: Record<Rag, number> = {
  unknown: 0,
  green: 1,
  amber: 2,
  red: 3,
};

const ragColors = ["#d7dde5", "#2f9b68", "#d88a16", "#cf3f3f"];

export function HeatmapChart({ data }: { data: PortfolioHeatmapResponse | undefined }) {
  const elementRef = useRef<HTMLDivElement | null>(null);
  const rows = useMemo(() => (data?.rows.length ? data.rows : ["portfolio"]), [data]);
  const columns = useMemo(() => (data?.columns.length ? data.columns : ["no-data"]), [data]);
  const cells = useMemo(() => data?.cells ?? [], [data]);
  const option = useMemo<EChartsOption>(
    () => ({
      animation: false,
      grid: { top: 12, right: 16, bottom: 32, left: 84 },
      tooltip: {
        formatter: (params: unknown) => {
          const value = cellValue(params);
          const cell = cells.find(
            (item) =>
              item.column === columns[value.columnIndex] && item.row === rows[value.rowIndex],
          );
          if (!cell) {
            return "No rollup data";
          }
          return [
            `<strong>${cell.entity_ref.kind}:${cell.entity_ref.id}</strong>`,
            `RAG: ${cell.rag}`,
            `Source: ${cell.source}`,
            cell.why,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: columns,
        axisLabel: { color: "#526070", fontSize: 11 },
        axisLine: { lineStyle: { color: "#cdd5df" } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "category",
        data: rows,
        axisLabel: { color: "#526070", fontSize: 11 },
        axisLine: { lineStyle: { color: "#cdd5df" } },
        axisTick: { show: false },
      },
      visualMap: {
        min: 0,
        max: 3,
        show: false,
        inRange: { color: ragColors },
      },
      series: [
        {
          type: "heatmap",
          data: cells.map((cell) => [
            columns.indexOf(cell.column),
            rows.indexOf(cell.row),
            ragScores[cell.rag],
          ]),
          label: {
            show: true,
            formatter: (params: unknown) => {
              const score = cellValue(params).score;
              return score === 0 ? "?" : score === 1 ? "G" : score === 2 ? "A" : "R";
            },
            color: "#111827",
            fontSize: 11,
            fontWeight: 600,
          },
          itemStyle: {
            borderColor: "#ffffff",
            borderWidth: 2,
          },
        },
      ],
    }),
    [cells, columns, rows],
  );

  useEffect(() => {
    if (!elementRef.current) {
      return undefined;
    }
    const chart = echarts.init(elementRef.current);
    chart.setOption(option);
    const resizeObserver = new ResizeObserver(() => chart.resize());
    resizeObserver.observe(elementRef.current);
    return () => {
      resizeObserver.disconnect();
      chart.dispose();
    };
  }, [option]);

  return <div ref={elementRef} className="h-72 w-full min-w-0" />;
}

function cellValue(params: unknown): { columnIndex: number; rowIndex: number; score: number } {
  if (
    typeof params === "object" &&
    params !== null &&
    "value" in params &&
    Array.isArray(params.value)
  ) {
    return {
      columnIndex: Number(params.value[0] ?? 0),
      rowIndex: Number(params.value[1] ?? 0),
      score: Number(params.value[2] ?? 0),
    };
  }
  return { columnIndex: 0, rowIndex: 0, score: 0 };
}
