// The assistant's conversation as the server reads it, and its answers as a
// reader does. Pure, with type imports and pure modules by their .ts path, so
// `node --test` runs it as written.
import type { AskConversation, AskSourceResponse, AskTurn } from "../../api/schema";
import type { AskMessage } from "./assistantContext";

/** What the request may carry (core/application/ask_conversation.py). */
export const MAX_TURNS = 40;
export const MAX_TURN_CHARS = 4000;

/**
 * What the server has folded away: its summary of the oldest turns, and how
 * many turns that summary covers. Those turns are not sent again.
 */
export type Memory = { summary: string | null; covered: number };

export const freshMemory = (): Memory => ({ summary: null, covered: 0 });

/** Every answered question and its answer, oldest first. A failed one is left out. */
export function turnsOf(messages: AskMessage[]): AskTurn[] {
  const turns: AskTurn[] = [];
  messages.forEach((message, index) => {
    const reply = messages[index + 1];
    if (message.from === "you" && reply?.from === "assistant" && reply.status === "answered") {
      turns.push(
        { role: "user", content: message.text },
        { role: "assistant", content: reply.text },
      );
    }
  });
  return turns;
}

/** What to send with the next question: the summary and the turns since it, or null for a first one. */
export function conversationFor(messages: AskMessage[], memory: Memory): AskConversation | null {
  const turns = turnsOf(messages)
    .slice(memory.covered)
    .slice(-MAX_TURNS)
    .map((turn) => ({ ...turn, content: turn.content.slice(0, MAX_TURN_CHARS) }));
  if (turns.length === 0 && !memory.summary) return null;
  return { summary: memory.summary, turns };
}

/** The memory after a reply: when the server folded turns away, keep its summary instead. */
export function remember(
  memory: Memory,
  reply: { summary?: string | null; summarized_turns?: number },
): Memory {
  const folded = reply.summarized_turns ?? 0;
  if (folded <= 0) return memory;
  return { summary: reply.summary ?? memory.summary, covered: memory.covered + folded };
}

/** An answer as a reader takes it in: the verdict, the bullets, other lines, and what is not known. */
export type AnswerParts = {
  verdict: string | null;
  bullets: string[];
  rest: string[];
  notKnown: string | null;
};

export function answerParts(text: string): AnswerParts {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const parts: AnswerParts = { verdict: null, bullets: [], rest: [], notKnown: null };
  lines.forEach((line, index) => {
    if (/^[•*-]\s+/.test(line)) parts.bullets.push(line.replace(/^[•*-]\s+/, ""));
    else if (/^not known:/i.test(line)) parts.notKnown = line.replace(/^not known:\s*/i, "");
    else if (index === 0) parts.verdict = line;
    else parts.rest.push(line);
  });
  return parts;
}

/**
 * The questions offered below the input: none while an answer is on its way;
 * after an answer, the ones it suggested; before any, the page's own. None is
 * one already asked.
 */
export function nextQuestions(
  messages: AskMessage[],
  pageQuestions: string[],
  pending: boolean,
): string[] {
  if (pending) return [];
  const asked = new Set(messages.flatMap((m) => (m.from === "you" ? [m.text] : [])));
  const last = [...messages].reverse().find((m) => m.from === "assistant");
  const suggested = last?.from === "assistant" && last.status === "answered" ? last.followUps : [];
  const offered = suggested.length > 0 ? suggested : pageQuestions;
  return offered.filter((question) => !asked.has(question));
}

/** Each source once by the words it reads as: a task and its issue can share a name. */
export function uniqueSources(sources: AskSourceResponse[]): AskSourceResponse[] {
  const seen = new Set<string>();
  return sources.filter((source) => {
    const key = (source.label || source.id).toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
