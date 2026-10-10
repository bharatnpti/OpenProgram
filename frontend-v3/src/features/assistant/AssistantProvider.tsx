import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import {
  ControlContext,
  StateContext,
  type AskMessage,
  type Control,
  type State,
} from "./assistantContext";
import { answering, applyEvent, streamEnded, type AskMode, type Answering } from "./investigate";
import { askError, type AskSubject } from "./persona";

/**
 * The assistant's conversation, held here for the whole console, so it stays
 * while the person moves between tabs (it goes with a reload). POST /ask is
 * single-turn: each question is sent on its own, with the past day shown as
 * its as_of. An investigation (POST /ask/investigate) is too, and its answer
 * fills in as the stream reports each step. Another person, or another lens,
 * starts with an empty conversation: what one asked is never shown to the
 * next, and an investigation still running for them is stopped.
 */
export function AssistantProvider({ children }: { children: ReactNode }) {
  const { access, actingAs, user, lens } = useRole();
  const { chosen, label } = useViewingDate();
  const owner = `${actingAs?.id ?? user?.subject ?? ""}|${lens.join(",")}`;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [subject, setSubject] = useState<AskSubject | null>(null);
  const [talk, setTalk] = useState<{ owner: string; messages: AskMessage[] }>({
    owner,
    messages: [],
  });
  const nextId = useRef(1);
  const running = useRef(new Set<AbortController>());
  const messages = talk.owner === owner ? talk.messages : [];
  const pending = messages.some((m) => m.from === "assistant" && m.status === "pending");

  const control = useMemo<Control>(
    () => ({
      openWith: (text) => {
        if (text !== undefined) setDraft(text);
        setOpen(true);
      },
      setSubject,
    }),
    [],
  );

  const ask = (question: string, mode: AskMode = "quick") => {
    const text = question.trim();
    if (!text || pending) return;
    const asked = owner;
    const you = nextId.current++;
    const answer = nextId.current++;
    setDraft("");
    setTalk((current) => ({
      owner: asked,
      messages: [
        ...(current.owner === asked ? current.messages : []),
        { id: you, from: "you", text },
        {
          id: answer,
          from: "assistant",
          status: "pending",
          text: "",
          sources: [],
          dayLabel: label,
          question: text,
          mode,
          steps: [],
        },
      ],
    }));
    const settle = (change: Partial<Extract<AskMessage, { from: "assistant" }>>) =>
      setTalk((current) =>
        current.owner !== asked
          ? current
          : {
              owner: asked,
              messages: current.messages.map((m) =>
                m.id === answer && m.from === "assistant" ? { ...m, ...change } : m,
              ),
            },
      );
    // Asking is a read: it is sent on a past day too, about that day.
    const body = chosen ? { question: text, as_of: chosen } : { question: text };
    if (mode === "investigate") {
      const stop = new AbortController();
      running.current.add(stop);
      let now: Answering = answering();
      apiClient
        .investigate(
          body,
          (event) => {
            now = applyEvent(now, event);
            settle(now);
          },
          stop.signal,
        )
        .then(() => settle(streamEnded(now)))
        .catch((error: unknown) => {
          if (!stop.signal.aborted) settle({ ...now, status: "failed", text: askError(error) });
        })
        .finally(() => running.current.delete(stop));
      return;
    }
    apiClient
      .ask(body)
      .then((reply) =>
        settle({ status: "answered", text: reply.answer, sources: reply.sources ?? [] }),
      )
      .catch((error: unknown) => settle({ status: "failed", text: askError(error) }));
  };

  // Another person or lens: the investigations asked for the last one stop.
  useEffect(() => {
    const investigations = running.current;
    return () => {
      investigations.forEach((stop) => stop.abort());
      investigations.clear();
    };
  }, [owner]);

  // Shut when the role loses it (a lens switched to a developer's).
  useEffect(() => {
    if (!access.assistant) setOpen(false);
  }, [access.assistant]);

  const state: State = {
    available: access.assistant,
    open: open && access.assistant,
    setOpen,
    draft,
    setDraft,
    messages,
    pending,
    ask,
    subject,
  };

  return (
    <ControlContext.Provider value={control}>
      <StateContext.Provider value={state}>{children}</StateContext.Provider>
    </ControlContext.Provider>
  );
}
