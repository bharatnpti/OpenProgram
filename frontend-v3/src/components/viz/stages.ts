// The six delivery stages and their colours: the one source every chart, list and
// admin tab that shows a stage reads (Overall's flow and requirement list, Daily's
// stage strip, Admin › Delivery stages and Gates). The colours are the
// `--op-stage-*` tokens in index.css, light and dark; a stage colour means a stage
// and nothing else. Type imports only, so `node --test` runs it as written.
import type { DeliveryStage } from "../../api/schema";

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
