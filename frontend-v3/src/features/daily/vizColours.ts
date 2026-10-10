// Daily's colours, as CSS variables (daily.css). Green, amber and red say a verdict or
// a gate state only; the ask kinds have their own four hues, used in D3 only.
import type { NeedType, Tone } from "./dailyViz";

export const TONE_STROKE: Record<Tone | "bypassed", string> = {
  green: "var(--op-day-green)",
  amber: "var(--op-day-amber)",
  red: "var(--op-day-red-solid)",
  bypassed: "var(--op-day-red-solid)",
  neutral: "var(--op-day-neutral-bg)",
};

export const TONE_TEXT: Record<Tone, string> = {
  green: "var(--op-day-green)",
  amber: "var(--op-day-amber)",
  red: "var(--op-day-red)",
  neutral: "var(--op-day-secondary)",
};

export const TONE_CHIP: Record<Tone | "info", { background: string; color: string }> = {
  green: { background: "var(--op-day-green-bg)", color: "var(--op-day-green)" },
  amber: { background: "var(--op-day-amber-bg)", color: "var(--op-day-amber)" },
  red: { background: "var(--op-day-red-bg)", color: "var(--op-day-red)" },
  info: { background: "var(--op-day-info-bg)", color: "var(--op-day-info)" },
  neutral: { background: "var(--op-day-neutral-bg)", color: "var(--op-day-body)" },
};

export const NEED_COLOUR: Record<NeedType, string> = {
  fix: "var(--op-day-ask-fix)",
  decision: "var(--op-day-ask-decision)",
  answer: "var(--op-day-ask-answer)",
  review: "var(--op-day-ask-review)",
};
