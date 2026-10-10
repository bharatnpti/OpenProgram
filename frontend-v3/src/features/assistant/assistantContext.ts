import { createContext, useContext, useEffect } from "react";

import type { AskSourceResponse, InvestigateStepResponse } from "../../api/schema";
import type { AskMode } from "./investigate";
import type { AskSubject } from "./persona";

export type AskMessage =
  | { id: number; from: "you"; text: string }
  | {
      id: number;
      from: "assistant";
      status: "pending" | "answered" | "failed";
      text: string;
      sources: AskSourceResponse[];
      /** The past day the question was asked about ("Mon 5 Oct"), or null for today. */
      dayLabel: string | null;
      /** The question this answers, so a quick answer can be investigated. */
      question: string;
      mode: AskMode;
      /** An investigation's steps, as the stream reports them; none for a quick answer. */
      steps: InvestigateStepResponse[];
      /** Questions the answer suggests asking next. */
      followUps: string[];
    };

export type Control = {
  /** Open the panel, with `draft` in the input (not sent) when given. */
  openWith: (draft?: string) => void;
  /** What the page shows, for the suggested questions; null when it shows no one thing. */
  setSubject: (subject: AskSubject | null) => void;
};

export type State = {
  /** Offered to this role at all (app/access.ts `assistant`). */
  available: boolean;
  open: boolean;
  setOpen: (open: boolean) => void;
  draft: string;
  setDraft: (text: string) => void;
  messages: AskMessage[];
  /** A question is out and not answered yet. */
  pending: boolean;
  /** Ask quickly (POST /ask), or investigate (POST /ask/investigate); the mode chosen when unset. */
  ask: (question: string, mode?: AskMode) => void;
  /** How the next question is asked, chosen in the composer; the panel and the page share it. */
  mode: AskMode;
  setMode: (mode: AskMode) => void;
  /** Start again: the conversation, its memory and anything still running go. */
  newChat: () => void;
  subject: AskSubject | null;
};

export const ControlContext = createContext<Control | null>(null);
export const StateContext = createContext<State | null>(null);

/** The conversation and the panel's state, for the panel itself. */
export function useAssistant(): State {
  const context = useContext(StateContext);
  if (!context) throw new Error("useAssistant must be used within AssistantProvider");
  return context;
}

/** Open the assistant from elsewhere (the ⌘K palette); null outside the provider. */
export function useAssistantControl(): Control | null {
  return useContext(ControlContext);
}

/**
 * A page says what it shows, so the assistant suggests questions about it:
 * a scrum master's pod, a product owner's project, a report's project, a
 * Delivery panel. Cleared when the page goes. Does nothing outside the provider.
 */
export function useAssistantSubject(subject: AskSubject | null): void {
  const set = useContext(ControlContext)?.setSubject;
  const kind = subject?.kind;
  const name = subject?.name;
  useEffect(() => {
    if (!set) return;
    set(kind && name ? { kind, name } : null);
    return () => set(null);
  }, [set, kind, name]);
}
