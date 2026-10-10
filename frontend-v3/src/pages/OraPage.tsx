import { ArrowLeft, MessageCircleMore, SquarePen } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { HeaderButton } from "../features/assistant/Assistant";
import { useAssistant } from "../features/assistant/assistantContext";
import { ChatView } from "../features/assistant/ChatView";
import { ASSISTANT_NAME, NEW_CHAT_LABEL } from "../features/assistant/persona";

/**
 * Ora in the whole tab (/ora): the same conversation as the panel, opened from
 * its header, with room to read. Back returns to the page it was opened from.
 * A role without the assistant has no page here and lands on Today.
 */
export function OraPage() {
  const assistant = useAssistant();
  const location = useLocation();
  const input = useRef<HTMLInputElement>(null);
  const from = (location.state as { from?: string } | null)?.from ?? "/today";

  useEffect(() => {
    input.current?.focus();
  }, []);

  if (!assistant.available) return <Navigate to="/today" replace />;

  return (
    <section
      aria-label={ASSISTANT_NAME}
      className="mx-auto flex h-[calc(100dvh-12rem)] min-h-[28rem] max-w-3xl flex-col overflow-hidden rounded-3xl border border-grey-border bg-white"
    >
      <header className="flex flex-none items-center gap-2 border-b border-grey-border px-6 py-3">
        <Link
          to={from}
          className="flex items-center gap-1.5 rounded-full px-2 py-1.5 text-[13px] font-bold text-grey-body no-underline hover:bg-grey-fill hover:text-ink"
        >
          <ArrowLeft size={16} aria-hidden />
          Back
        </Link>
        <span
          aria-hidden
          className="ml-1 flex h-8 w-8 flex-none items-center justify-center rounded-full bg-ink text-white"
        >
          <MessageCircleMore size={16} />
        </span>
        <h1 className="min-w-0 flex-1 text-[17px] font-extrabold leading-tight">
          {ASSISTANT_NAME}
          <span className="block text-[11px] font-medium text-grey-secondary">
            Asks the delivery data for you
          </span>
        </h1>
        <HeaderButton
          label={NEW_CHAT_LABEL}
          disabled={assistant.messages.length === 0}
          onClick={() => {
            assistant.newChat();
            input.current?.focus();
          }}
        >
          <SquarePen size={17} aria-hidden />
        </HeaderButton>
      </header>
      <ChatView variant="page" inputRef={input} />
    </section>
  );
}
