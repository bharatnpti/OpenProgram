// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type { DeliveryStage, Rag, Verdict } from "../api/schema";

export type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

export function toneForRag(rag: Rag | null | undefined): BadgeTone {
  if (rag === "green") return "success";
  if (rag === "amber") return "warning";
  if (rag === "red") return "danger";
  return "neutral";
}

/** Worst first, with `unknown` above `green`: silence is never a clean bill of health. */
export function ragSeverity(rag: Rag | null | undefined): number {
  if (rag === "red") return 3;
  if (rag === "amber") return 2;
  if (rag === "green") return 0;
  return 1;
}

export const VERDICT_LABELS: Record<Verdict, string> = {
  on_track: "On track",
  at_risk: "At risk",
  off_track: "Off track",
  done: "Done",
  no_date: "No committed date",
  not_enough_data: "Not enough history to forecast",
};

export function toneForVerdict(verdict: Verdict): BadgeTone {
  if (verdict === "on_track" || verdict === "done") return "success";
  if (verdict === "at_risk") return "warning";
  if (verdict === "off_track") return "danger";
  return "neutral";
}

/** The six delivery stages in the order a requirement moves through them. */
export const STAGE_ORDER: DeliveryStage[] = [
  "raised",
  "groomed",
  "in_development",
  "in_testing",
  "business_testing",
  "production",
];

/** Fallback wording; the requirements response carries the tenant's own labels. */
export const STAGE_LABELS: Record<DeliveryStage, string> = {
  raised: "Raised",
  groomed: "Groomed",
  in_development: "In development",
  in_testing: "In testing",
  business_testing: "Business testing",
  production: "Production",
};

/** CSS custom property for a stage's colour, defined in index.css. */
export function stageColor(stage: DeliveryStage): string {
  return `var(--op-stage-${stage.replace(/_/g, "-")})`;
}
