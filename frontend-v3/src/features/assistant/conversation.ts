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

/**
 * An answer as a reader takes it in, in its own order: lines of text, runs of
 * bullets as lists, and the "Not known:" line. The first line and any line
 * that ends in a colon (a section: "Identity Platform is red because:") are
 * headings.
 */
export type AnswerBlock =
  | { kind: "text"; text: string; heading: boolean }
  | { kind: "list"; items: string[] }
  | { kind: "notKnown"; text: string };

const BULLET = /^[•*-]\s+/;
const NOT_KNOWN = /^not known:\s*/i;

export function answerBlocks(text: string): AnswerBlock[] {
  const blocks: AnswerBlock[] = [];
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line, index) => {
      const last = blocks[blocks.length - 1];
      if (BULLET.test(line)) {
        const item = line.replace(BULLET, "");
        if (last?.kind === "list") last.items.push(item);
        else blocks.push({ kind: "list", items: [item] });
      } else if (NOT_KNOWN.test(line)) {
        blocks.push({ kind: "notKnown", text: line.replace(NOT_KNOWN, "") });
      } else {
        blocks.push({ kind: "text", text: line, heading: index === 0 || line.endsWith(":") });
      }
    });
  return blocks;
}

/**
 * A reply's answer and the questions to offer after it. The server cuts a
 * follow-ups list the model wrote into the answer ("follow_ups: [...]", one
 * "- question" to a line, or at the end of a line); should one get through, it
 * is cut here too, so it never shows in the bubble, and its questions are
 * offered when the reply has none of its own.
 */
export function readAnswer(
  text: string,
  followUps: string[] | null | undefined,
): { text: string; followUps: string[] } {
  const lines = text.split("\n");
  const kept: string[] = [];
  const asked: string[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const heading = FOLLOW_UPS_HEADING.exec(lines[index]);
    if (!heading) {
      const trailing = TRAILING_FOLLOW_UPS.exec(lines[index]);
      if (trailing) asked.push(...questionItems(trailing[1]));
      kept.push(trailing ? lines[index].slice(0, trailing.index) : lines[index]);
      continue;
    }
    let listed = heading[1];
    if (!listed.trim()) {
      while (index + 1 < lines.length && !lines[index + 1].trim()) index += 1;
      if (index + 1 < lines.length && lines[index + 1].trim().startsWith("[")) {
        index += 1;
        listed = lines[index];
      } else {
        while (index + 1 < lines.length && LIST_ITEM.test(lines[index + 1])) {
          index += 1;
          asked.push(question(lines[index].replace(LIST_ITEM, "")));
        }
      }
    }
    while (stillOpen(listed) && index + 1 < lines.length) {
      index += 1;
      listed = `${listed}\n${lines[index]}`;
    }
    asked.push(...questionItems(listed));
  }
  const own = (followUps ?? []).filter(Boolean);
  return {
    text: kept.join("\n").trim(),
    followUps: own.length > 0 ? own : [...new Set(asked.filter(Boolean))].slice(0, 3),
  };
}

// The field's own name, in any spelling: follow_ups, followUps, "follow_ups".
const FOLLOW_UPS_KEY = "follow_?ups|follow_?up_?questions";
const FOLLOW_UPS_HEADING = new RegExp(
  `^\\s*(?:[-*•]\\s*)?[*_\`"']*(?:${FOLLOW_UPS_KEY})[*_\`"']*\\s*[:=]\\s*(.*?)\\s*,?\\s*$`,
  "i",
);
const TRAILING_FOLLOW_UPS = new RegExp(
  `\\s*\\{?\\s*["']?\\b(?:${FOLLOW_UPS_KEY})["']?\\s*[:=]\\s*(\\[[^\\[\\]]*\\]?)\\s*\\}?\\s*[,.]?\\s*$`,
  "i",
);
const LIST_ITEM = /^\s*(?:[-*•]|\d+[.)])\s+/;
const QUOTED = /"((?:[^"\\]|\\.)*)"/g;

const stillOpen = (text: string) =>
  text.split("[").length > text.split("]").length && text.trim().startsWith("[");

function questionItems(text: string): string[] {
  const listed = text.trim().replace(/[,.]$/, "").trim();
  if (!listed) return [];
  if (listed.startsWith("[")) {
    try {
      const decoded: unknown = JSON.parse(listed);
      if (Array.isArray(decoded)) {
        return decoded.filter((item): item is string => typeof item === "string").map(question);
      }
    } catch {
      // Not JSON: read the items one by one.
    }
  }
  const bare = listed.replace(/^\[|\]$/g, "");
  const quoted = [...bare.matchAll(QUOTED)].map((match) => match[1]);
  const leftover = bare.replace(QUOTED, "").replace(/[\s,]/g, "");
  if (quoted.length > 0 && (!leftover || /^"[^"]*$/.test(leftover))) {
    return quoted.map((item) => question(item.replace(/\\"/g, '"')));
  }
  return bare.split(bare.includes("?") ? /(?<=\?)["'`]?[\s,;]+/ : /\s*[,;\n]\s*/).map(question);
}

const question = (item: string) =>
  item
    .trim()
    .replace(/^["'`*_\s]+|["'`*_\s,]+$/g, "")
    .trim();

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
