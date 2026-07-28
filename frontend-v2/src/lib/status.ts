import type { Rag, StatusSource } from "../api/schema";

export type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

export function toneForRag(rag: Rag | null | undefined): BadgeTone {
  if (rag === "green") return "success";
  if (rag === "amber") return "warning";
  if (rag === "red") return "danger";
  if (rag === "unknown") return "neutral";
  return "neutral";
}

export function toneForSource(source: StatusSource | undefined): BadgeTone {
  if (source === "confirmed") return "success";
  if (source === "partial") return "info";
  if (source === "stale") return "warning";
  if (source === "inferred") return "info";
  if (source === "unknown" || !source) return "neutral";
  return "neutral";
}

export function sourceLine(source: StatusSource, confidence: number | null): string {
  return `${source}${confidence === null ? "" : ` / ${Math.round(confidence * 100)}%`}`;
}

export const toneHex: Record<BadgeTone, string> = {
  success: "var(--op-green)",
  warning: "var(--op-amber)",
  danger: "var(--op-red)",
  info: "var(--op-info)",
  neutral: "var(--op-unknown)",
};
