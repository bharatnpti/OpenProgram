import * as Dialog from "@radix-ui/react-dialog";
import { Maximize2, MessageCircleMore, Minimize2, SendHorizontal, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { paletteTargets } from "../../app/access";
import { useRole } from "../../app/role";
import { useDayWords, useViewingDate } from "../../app/viewingDate";
import { cn } from "../../lib/utils";
import { useAssistant, type AskMessage } from "./assistantContext";
import {
  ANSWER_NOTE,
  ASK_LABEL,
  ASSISTANT_NAME,
  askedAboutWords,
  assistantGreeting,
  placeOf,
  sourceLink,
  sourceWords,
  suggestionsFor,
} from "./persona";

const INPUT_ID = "assistant-question";
const PHONE = "(max-width: 639px)";

/**
 * The assistant, on every tab for the roles that may ask: a round button at
 * the bottom right that opens a chat panel. It greets the person by name,
 * suggests questions that fit the page and what it shows, and answers from
 * POST /ask, each answer with its sources as links where the role has their
 * page. The panel is not modal: the page stays usable beside it. Esc closes it
 * and focus goes back to the button; on a phone it fills the screen. Motion
 * stops for prefers-reduced-motion (index.css).
 */
export function Assistant() {
  const assistant = useAssistant();
  const roleState = useRole();
  const { access, greetingName, canReadPodDetail } = roleState;
  const { pathname, search } = useLocation();
  const { label } = useViewingDate();
  const day = useDayWords();
  const [tall, setTall] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const targets = useMemo(() => paletteTargets(access), [access]);
  const place = {
    ...placeOf(pathname, search, access.signals.defaultView),
    subject: assistant.subject,
  };
  // A question already asked in this conversation is not offered again.
  const asked = new Set(
    assistant.messages.flatMap((message) => (message.from === "you" ? [message.text] : [])),
  );
  const chips = suggestionsFor(place, { podDetail: canReadPodDetail, day }).filter(
    (chip) => !asked.has(chip),
  );
  const askedAbout = askedAboutWords(label);
  const last = assistant.messages[assistant.messages.length - 1];
  const lastState = last ? `${last.id}:${last.from === "assistant" ? last.status : ""}` : "";

  // The newest message in view; no smooth scroll, so nothing moves for reduced motion.
  useEffect(() => {
    if (assistant.open) end.current?.scrollIntoView({ block: "end" });
  }, [assistant.open, lastState]);

  if (!assistant.available) return null;

  const send = (question: string) => {
    assistant.ask(question);
    input.current?.focus();
  };

  return (
    <Dialog.Root open={assistant.open} onOpenChange={assistant.setOpen} modal={false}>
      <Dialog.Trigger asChild>
        <button
          type="button"
          aria-label={assistant.open ? `Close ${ASSISTANT_NAME}` : ASK_LABEL}
          title={assistant.open ? `Close ${ASSISTANT_NAME}` : ASK_LABEL}
          className="fixed bottom-5 right-5 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-ink text-white shadow-op-menu transition-colors hover:bg-ink-hover sm:bottom-6 sm:right-6"
        >
          {assistant.open ? (
            <X size={24} aria-hidden />
          ) : (
            <MessageCircleMore size={26} aria-hidden />
          )}
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Content
          aria-describedby={undefined}
          // The question field first, so a person can type at once.
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            input.current?.focus();
          }}
          // The page stays usable beside the panel: a click on it does not close it.
          onInteractOutside={(event) => event.preventDefault()}
          className={cn(
            "fixed inset-0 z-50 flex flex-col bg-white animate-op-pop",
            "sm:inset-auto sm:bottom-24 sm:right-6 sm:w-[min(420px,calc(100vw-3rem))] sm:rounded-3xl sm:border sm:border-grey-border sm:shadow-op-palette",
            tall ? "sm:top-4" : "sm:h-[min(640px,calc(100dvh-8rem))]",
          )}
        >
          <header className="flex flex-none items-center gap-2 border-b border-grey-border px-4 py-3">
            <span
              aria-hidden
              className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-ink text-white"
            >
              <MessageCircleMore size={18} />
            </span>
            <Dialog.Title className="min-w-0 flex-1 leading-tight">
              <span className="block text-[16px] font-extrabold">{ASSISTANT_NAME}</span>
              <span className="block text-[11px] font-medium text-grey-secondary">
                Asks the delivery data for you
              </span>
            </Dialog.Title>
            <button
              type="button"
              aria-pressed={tall}
              aria-label={tall ? "Back to a smaller panel" : "Expand to full height"}
              title={tall ? "Back to a smaller panel" : "Expand to full height"}
              onClick={() => setTall((on) => !on)}
              className="hidden h-9 w-9 items-center justify-center rounded-full text-grey-body hover:bg-grey-fill sm:flex"
            >
              {tall ? <Minimize2 size={17} aria-hidden /> : <Maximize2 size={17} aria-hidden />}
            </button>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label={`Close ${ASSISTANT_NAME}`}
                title={`Close ${ASSISTANT_NAME} (Esc)`}
                className="flex h-9 w-9 items-center justify-center rounded-full text-grey-body hover:bg-grey-fill"
              >
                <X size={19} aria-hidden />
              </button>
            </Dialog.Close>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <p className="text-[21px] font-extrabold leading-tight text-balance">
              {assistantGreeting(greetingName)}
            </p>
            <p className="mt-1 text-[13px] text-grey-body">
              I&apos;m {ASSISTANT_NAME}. I answer from OpenProgram&apos;s check-ins, blockers,
              risks, review flow and status, and only what your role may read.
            </p>
            {askedAbout ? (
              <p className="mt-2 rounded-xl bg-rag-amber-bg px-3 py-1.5 text-[12px] font-bold text-rag-amber">
                {askedAbout}
              </p>
            ) : null}

            <ol aria-label="Conversation" role="log" aria-live="polite" className="mt-4 grid gap-3">
              {assistant.messages.map((message) => (
                <Bubble
                  key={message.id}
                  message={message}
                  linkFor={(source) => sourceLink(source, targets)}
                  onLink={() => {
                    // On a phone the panel covers the page the link opens.
                    if (window.matchMedia?.(PHONE).matches) assistant.setOpen(false);
                  }}
                />
              ))}
            </ol>

            <div className={cn("mt-4", chips.length === 0 && "hidden")}>
              <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
                {assistant.messages.length > 0 ? "More to ask here" : "Try asking"}
              </p>
              <ul className="flex flex-col items-start gap-2">
                {chips.map((chip) => (
                  <li key={chip} className="max-w-full">
                    <button
                      type="button"
                      disabled={assistant.pending}
                      onClick={() => send(chip)}
                      className="max-w-full rounded-2xl border border-grey-border bg-grey-fill px-3.5 py-2 text-left text-[13px] font-bold text-ink hover:border-ink disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {chip}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            <div ref={end} />
          </div>

          <form
            className="flex-none border-t border-grey-border px-4 pb-3 pt-3"
            onSubmit={(event) => {
              event.preventDefault();
              send(assistant.draft);
            }}
          >
            <div className="flex items-center gap-2 rounded-full border border-grey-border py-1 pl-4 pr-1 focus-within:border-ink focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ink">
              <label htmlFor={INPUT_ID} className="sr-only">
                Your question for {ASSISTANT_NAME}
              </label>
              <input
                ref={input}
                id={INPUT_ID}
                value={assistant.draft}
                onChange={(event) => assistant.setDraft(event.target.value)}
                maxLength={500}
                autoComplete="off"
                placeholder="Ask about dates, blockers, risks…"
                className="min-w-0 flex-1 bg-transparent py-2 text-[14px] outline-none placeholder:text-grey-secondary"
              />
              <button
                type="submit"
                aria-label="Send"
                title="Send"
                disabled={!assistant.draft.trim() || assistant.pending}
                className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-ink text-white hover:bg-ink-hover disabled:cursor-not-allowed disabled:bg-grey-disabled"
              >
                <SendHorizontal size={16} aria-hidden />
              </button>
            </div>
            <p className="mt-2 text-center text-[11px] text-grey-secondary">{ANSWER_NOTE}</p>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Bubble({
  message,
  linkFor,
  onLink,
}: {
  message: AskMessage;
  linkFor: (source: Extract<AskMessage, { from: "assistant" }>["sources"][number]) => string | null;
  onLink: () => void;
}) {
  if (message.from === "you") {
    return (
      <li className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-line rounded-2xl rounded-br-md bg-ink px-3.5 py-2 text-[14px] text-white">
          <span className="sr-only">You: </span>
          {message.text}
        </p>
      </li>
    );
  }
  if (message.status === "pending") {
    return (
      <li>
        <p className="inline-block rounded-2xl rounded-bl-md bg-grey-fill px-3.5 py-2 text-[14px] text-grey-secondary">
          {ASSISTANT_NAME} is looking it up…
        </p>
      </li>
    );
  }
  const failed = message.status === "failed";
  return (
    <li className="max-w-[92%]">
      <p
        className={cn(
          "whitespace-pre-line rounded-2xl rounded-bl-md px-3.5 py-2.5 text-[14px]",
          failed ? "bg-rag-red-bg font-bold text-rag-red" : "bg-grey-fill text-ink",
        )}
      >
        <span className="sr-only">{ASSISTANT_NAME}: </span>
        {message.text}
      </p>
      {message.dayLabel || message.sources.length > 0 ? (
        <p className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-1 text-[12px] text-grey-secondary">
          {message.dayLabel ? <span>About {message.dayLabel}</span> : null}
          {message.sources.length > 0 ? <span>Sources:</span> : null}
          {message.sources.map((source) => {
            const { label, kind } = sourceWords(source);
            const to = linkFor(source);
            return to ? (
              <Link
                key={source.id}
                to={to}
                title={kind ?? undefined}
                onClick={onLink}
                className="font-bold text-ink hover:text-grey-body"
              >
                {label}
              </Link>
            ) : (
              <span key={source.id} title={kind ?? undefined} className="font-bold text-grey-body">
                {label}
              </span>
            );
          })}
        </p>
      ) : null}
    </li>
  );
}
