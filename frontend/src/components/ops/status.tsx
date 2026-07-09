import type { Rag, StatusSource } from "../../api/schema";
import { Badge } from "../ui/badge";
import { toneForRag, toneForSource, type BadgeTone } from "./status-utils";

export function StatusBadge({ rag }: { rag: Rag | null | undefined }) {
  return <Badge tone={toneForRag(rag)}>{rag ?? "unknown"}</Badge>;
}

export function StateBadge({
  state,
}: {
  state: "confirmed" | "partial" | "stale" | "missing" | string;
}) {
  const tone: BadgeTone =
    state === "confirmed"
      ? "success"
      : state === "partial"
        ? "info"
        : state === "stale"
          ? "warning"
          : "danger";
  return <Badge tone={tone}>{state}</Badge>;
}

export function SourceConfidence({
  source,
  confidence,
}: {
  source?: StatusSource;
  confidence?: number | null;
}) {
  const confidenceLabel =
    typeof confidence === "number" ? `${Math.round(confidence * 100)}%` : "no score";
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <Badge tone={toneForSource(source)}>{source ?? "unknown"}</Badge>
      <span>{confidenceLabel}</span>
    </div>
  );
}
