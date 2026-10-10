// The colour of a tone, for text and for a tinted fill, from the RAG tokens in
// index.css: what a date, a forecast or a cause line is painted with.
import type { BadgeTone } from "../../lib/status";

/** Text in the tone's colour; neutral keeps the text it sits in. */
export const TONE_TEXT: Record<BadgeTone, string> = {
  success: "text-rag-green",
  warning: "text-rag-amber",
  danger: "text-rag-red",
  info: "text-rag-info",
  neutral: "",
};

/** A tinted background in the tone; neutral has none. */
export const TONE_FILL: Record<BadgeTone, string> = {
  success: "bg-rag-green-bg",
  warning: "bg-rag-amber-bg",
  danger: "bg-rag-red-bg",
  info: "bg-rag-info-bg",
  neutral: "",
};
