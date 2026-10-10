import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { apiClient } from "../../api/client";
import type { AskResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import {
  ControlContext,
  StateContext,
  type AskMessage,
  type Control,
  type State,
} from "./assistantContext";
import { conversationFor, freshMemory, remember, type Memory } from "./conversation";
import { answering, applyEvent, streamEnded, type AskMode, type Answering } from "./investigate";
import { askError, type AskSubject } from "./persona";

type Talk = { owner: string; messages: AskMessage[]; memory: Memory };

/**
 * The assistant's conversation, held here for the whole console, so it stays
 * while the person moves between tabs and between the panel and Ora's page
 * (it goes with a reload). Each question is sent with the conversation before
 * it -- the turns since the server's last summary, and that summary -- so a
 * follow-up means what the person meant; the server folds a long one into a
 * summary and says so in its reply. An investigation (POST /ask/investigate)
 * fills its answer in as the stream reports each step. New chat starts again.
 * Another person, or another lens, starts with an empty conversation: what one
 * asked is never shown to the next, and an investigation still running for
 * them is stopped.
 */
export function AssistantProvider({ children }: { children: ReactNode }) {
  const { access, actingAs, user, lens } = useRole();
  const { chosen, label } = useViewingDate();
  const owner = `${actingAs?.id ?? user?.subject ?? ""}|${lens.join(",")}`;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<AskMode>("quick");
  const [subject, setSubject] = useState<AskSubject | null>(null);
  const [talk, setTalk] = useState<Talk>({ owner, messages: [], memory: freshMemory() });
  const nextId = useRef(1);
  const running = useRef(new Set<AbortController>());
  const current = talk.owner === owner ? talk : { owner, messages: [], memory: freshMemory() };
  const messages = current.messages;
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

  const stopRunning = useCallback(() => {
    running.current.forEach((stop) => stop.abort());
    running.current.clear();
  }, []);

  const newChat = () => {
    stopRunning();
    setTalk({ owner, messages: [], memory: freshMemory() });
  };

  const ask = (question: string, asked: AskMode = mode) => {
    const text = question.trim();
    if (!text || pending) return;
    const who = owner;
    const you = nextId.current++;
    const answer = nextId.current++;
    // What was said before this question; the question itself goes on its own.
    const conversation = conversationFor(messages, current.memory);
    setDraft("");
    setTalk((was) => ({
      owner: who,
      memory: was.owner === who ? was.memory : freshMemory(),
      messages: [
        ...(was.owner === who ? was.messages : []),
        { id: you, from: "you", text },
        {
          id: answer,
          from: "assistant",
          status: "pending",
          text: "",
          sources: [],
          dayLabel: label,
          question: text,
          mode: asked,
          steps: [],
          followUps: [],
        },
      ],
    }));
    const settle = (change: Partial<Extract<AskMessage, { from: "assistant" }>>) =>
      setTalk((was) =>
        was.owner !== who
          ? was
          : {
              ...was,
              messages: was.messages.map((m) =>
                m.id === answer && m.from === "assistant" ? { ...m, ...change } : m,
              ),
            },
      );
    const keep = (reply: Pick<AskResponse, "summary" | "summarized_turns">) =>
      setTalk((was) => (was.owner !== who ? was : { ...was, memory: remember(was.memory, reply) }));
    // Asking is a read: it is sent on a past day too, about that day.
    const body = {
      question: text,
      ...(chosen ? { as_of: chosen } : {}),
      ...(conversation ? { conversation } : {}),
    };
    if (asked === "investigate") {
      const stop = new AbortController();
      running.current.add(stop);
      let now: Answering = answering();
      apiClient
        .investigate(
          body,
          (event) => {
            now = applyEvent(now, event);
            if (event.type === "answer") keep(event.answer);
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
      .then((reply) => {
        keep(reply);
        settle({
          status: "answered",
          text: reply.answer,
          sources: reply.sources ?? [],
          followUps: reply.follow_ups ?? [],
        });
      })
      .catch((error: unknown) => settle({ status: "failed", text: askError(error) }));
  };

  // Another person or lens: the investigations asked for the last one stop.
  useEffect(() => stopRunning, [owner, stopRunning]);

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
    mode,
    setMode,
    newChat,
    subject,
  };

  return (
    <ControlContext.Provider value={control}>
      <StateContext.Provider value={state}>{children}</StateContext.Provider>
    </ControlContext.Provider>
  );
}
