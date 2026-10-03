import type { AskResponse, AskSourceResponse } from "../../api/schema";

/** One line of an answer: the verdict and closing lines are text, the drivers bullets. */
export type AnswerLine = { kind: "text" | "bullet"; text: string };

/** An evidence chip under an answer. */
export type SourceChip = {
  key: string;
  /** What the reader knows it by: a name, an issue key, a merge request ref. */
  label: string;
  /** The kind and id, for a hover; the id alone when nothing matched it. */
  title: string;
  /** Where its Delivery panel is, for the kinds that have one. */
  to?: string;
};

const BULLET = /^\s*[•\-*]\s+/;
const DELIVERY_KINDS = new Set(["program", "project", "workstream", "pod"]);

/** Split an answer into its lines, marking the bullets. Blank lines are dropped. */
export function answerLines(answer: string): AnswerLine[] {
  return answer
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line): AnswerLine =>
      BULLET.test(line)
        ? { kind: "bullet", text: line.replace(BULLET, "") }
        : { kind: "text", text: line },
    );
}

/**
 * The chips for an answer's references, labelled the way a reader knows each
 * one. A reference the server could not match to anything shows its id rather
 * than a guessed name, and so does every reference from a server that sends
 * no labels at all.
 */
export function sourceChips(response: Pick<AskResponse, "references" | "sources">): SourceChip[] {
  const sources: AskSourceResponse[] =
    response.sources && response.sources.length > 0
      ? response.sources
      : response.references.map((id) => ({ id }));
  const seen = new Set<string>();
  const chips: SourceChip[] = [];
  sources.forEach((source) => {
    if (seen.has(source.id)) return;
    seen.add(source.id);
    const kind = source.kind ?? null;
    chips.push({
      key: source.id,
      label: source.label || source.id,
      title: kind ? `${kind.replace(/_/g, " ")} · ${source.id}` : source.id,
      to:
        kind && DELIVERY_KINDS.has(kind)
          ? `/delivery/${kind}/${encodeURIComponent(source.id)}`
          : undefined,
    });
  });
  return chips;
}
