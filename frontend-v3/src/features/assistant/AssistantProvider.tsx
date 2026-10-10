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
import { askError, type AskSubject } from "./persona";

/**
 * The assistant's conversation, held here for the whole console, so it stays
 * while the person moves between tabs (it goes with a reload). POST /ask is
 * single-turn: each question is sent on its own, with the past day shown as
 * its as_of. Another person, or another lens, starts with an empty
 * conversation: what one asked is never shown to the next.
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

  const ask = (question: string) => {
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
    apiClient
      .ask(chosen ? { question: text, as_of: chosen } : { question: text })
      .then((reply) =>
        settle({ status: "answered", text: reply.answer, sources: reply.sources ?? [] }),
      )
      .catch((error: unknown) => settle({ status: "failed", text: askError(error) }));
  };

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
