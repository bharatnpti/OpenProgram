import { Check, LoaderCircle, SendHorizontal, Telescope, X } from "lucide-react";
import { useEffect, useMemo, useRef, type RefObject } from "react";
import { Link, useLocation } from "react-router-dom";

import type { AskSourceResponse, InvestigateStepResponse } from "../../api/schema";
import { paletteTargets } from "../../app/access";
import { useRole } from "../../app/role";
import { useDayWords, useViewingDate } from "../../app/viewingDate";
import { cn } from "../../lib/utils";
import { useAssistant, type AskMessage } from "./assistantContext";
import { answerParts, nextQuestions, uniqueSources } from "./conversation";
import { toolWords } from "./investigate";
import {
  ANSWER_NOTE,
  ASSISTANT_NAME,
  INVESTIGATE_LABEL,
  INVESTIGATE_NOTE,
  INVESTIGATE_THIS,
  QUICK_LABEL,
  askedAboutWords,
  assistantGreeting,
  checkedWords,
  pendingWords,
  placeOf,
  sourceLink,
  sourceWords,
  sourcesWords,
  suggestionsFor,
} from "./persona";

const INPUT_ID = "assistant-question";

/**
 * Ora's conversation: the greeting, the messages, and the composer with the
 * questions to ask next below it. The panel and Ora's own page both show it,
 * on the one conversation the provider keeps. The questions to ask next go
 * while an answer is on its way and come back from it.
 */
export function ChatView({
  variant,
  inputRef,
  onLink,
}: {
  variant: "panel" | "page";
  inputRef: RefObject<HTMLInputElement | null>;
  /** Called when a source link is followed, so a panel that covers the page can shut. */
  onLink?: () => void;
}) {
  const assistant = useAssistant();
  const { access, greetingName, canReadPodDetail } = useRole();
  const { pathname, search } = useLocation();
  const { label } = useViewingDate();
  const day = useDayWords();
  const end = useRef<HTMLDivElement>(null);
  const targets = useMemo(() => paletteTargets(access), [access]);
  const place = {
    ...placeOf(pathname, search, access.signals.defaultView),
    subject: assistant.subject,
  };
  const next = nextQuestions(
    assistant.messages,
    suggestionsFor(place, { podDetail: canReadPodDetail, day }),
    assistant.pending,
  );
  const askedAbout = askedAboutWords(label);
  const investigating = assistant.mode === "investigate";
  const last = assistant.messages[assistant.messages.length - 1];
  const lastState = last
    ? `${last.id}:${last.from === "assistant" ? `${last.status}:${last.steps.length}` : ""}`
    : "";

  // The newest message in view; no smooth scroll, so nothing moves for reduced motion.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [lastState]);

  const send = (question: string) => {
    assistant.ask(question);
    inputRef.current?.focus();
  };

  return (
    <>
      <div
        className={cn("min-h-0 flex-1 overflow-y-auto py-4", variant === "page" ? "px-6" : "px-4")}
      >
        <p className="text-[21px] font-extrabold leading-tight text-balance">
          {assistantGreeting(greetingName)}
        </p>
        <p className="mt-1 text-[13px] text-grey-body">
          I&apos;m {ASSISTANT_NAME}. I answer from OpenProgram&apos;s check-ins, blockers, risks,
          review flow and status, and only what your role may read.
        </p>
        {askedAbout ? (
          <p className="mt-2 rounded-xl bg-rag-amber-bg px-3 py-1.5 text-[12px] font-bold text-rag-amber">
            {askedAbout}
          </p>
        ) : null}

        <ol aria-label="Conversation" role="log" aria-live="polite" className="mt-4 grid gap-4">
          {assistant.messages.map((message) => (
            <Bubble
              key={message.id}
              message={message}
              wide={variant === "page"}
              linkFor={(source) => sourceLink(source, targets)}
              onLink={onLink}
              busy={assistant.pending}
              onInvestigate={(question) => assistant.ask(question, "investigate")}
            />
          ))}
        </ol>
        <div ref={end} />
      </div>

      <form
        className={cn(
          "flex-none border-t border-grey-border pb-3 pt-3",
          variant === "page" ? "px-6" : "px-4",
        )}
        onSubmit={(event) => {
          event.preventDefault();
          send(assistant.draft);
        }}
      >
        <div
          role="group"
          aria-label="How to answer"
          className="mb-2 flex w-fit gap-1 rounded-full bg-grey-fill p-1 text-[12px] font-bold"
        >
          {(["quick", "investigate"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={assistant.mode === mode}
              onClick={() => assistant.setMode(mode)}
              className={cn(
                "flex items-center gap-1.5 rounded-full px-3 py-1.5",
                assistant.mode === mode
                  ? "bg-white text-ink shadow-op-menu"
                  : "text-grey-body hover:text-ink",
              )}
            >
              {mode === "investigate" ? <Telescope size={13} aria-hidden /> : null}
              {mode === "investigate" ? INVESTIGATE_LABEL : QUICK_LABEL}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 rounded-full border border-grey-border py-1 pl-4 pr-1 focus-within:border-ink focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ink">
          <label htmlFor={INPUT_ID} className="sr-only">
            Your question for {ASSISTANT_NAME}
          </label>
          <input
            ref={inputRef}
            id={INPUT_ID}
            value={assistant.draft}
            onChange={(event) => assistant.setDraft(event.target.value)}
            maxLength={500}
            autoComplete="off"
            placeholder={
              investigating
                ? "Ask why, or what is really holding something up…"
                : "Ask about dates, blockers, risks…"
            }
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
        {next.length > 0 ? (
          <div className="mt-2">
            <p className="sr-only">Questions to ask next</p>
            <ul className="flex flex-wrap gap-1.5">
              {next.map((question) => (
                <li key={question} className="max-w-full">
                  <button
                    type="button"
                    onClick={() => send(question)}
                    className="max-w-full rounded-full border border-grey-border bg-white px-3 py-1 text-left text-[12px] font-bold text-ink hover:border-ink"
                  >
                    {question}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="mt-2 text-center text-[11px] text-grey-secondary">
          {investigating ? INVESTIGATE_NOTE : ANSWER_NOTE}
        </p>
      </form>
    </>
  );
}

function Bubble({
  message,
  wide,
  linkFor,
  onLink,
  busy,
  onInvestigate,
}: {
  message: AskMessage;
  wide: boolean;
  linkFor: (source: AskSourceResponse) => string | null;
  onLink?: () => void;
  /** A question is out: nothing new is asked until it is answered. */
  busy: boolean;
  onInvestigate: (question: string) => void;
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
  const investigated = message.mode === "investigate";
  if (message.status === "pending") {
    return (
      <li className="max-w-[92%]">
        <div className="inline-block rounded-2xl rounded-bl-md bg-grey-fill px-3.5 py-2 text-[14px] text-grey-secondary">
          <p>{pendingWords(investigated, message.steps.length)}</p>
          {message.steps.length > 0 ? <Steps steps={message.steps} brief /> : null}
        </div>
      </li>
    );
  }
  const failed = message.status === "failed";
  const sources = uniqueSources(message.sources);
  return (
    <li className={wide ? "max-w-[min(100%,46rem)]" : "max-w-[92%]"}>
      <div
        className={cn(
          "rounded-2xl rounded-bl-md px-3.5 py-2.5 text-[14px] leading-relaxed",
          failed ? "bg-rag-red-bg font-bold text-rag-red" : "bg-grey-fill text-ink",
        )}
      >
        <span className="sr-only">{ASSISTANT_NAME}: </span>
        {failed ? message.text : <AnswerText text={message.text} />}
      </div>
      <div className="mt-1 grid gap-0.5 px-1 text-[12px]">
        {message.dayLabel ? <p className="text-grey-secondary">About {message.dayLabel}</p> : null}
        {sources.length > 0 ? (
          <details>
            <summary className="cursor-pointer font-bold text-grey-body hover:text-ink">
              {sourcesWords(sources.length)}
            </summary>
            <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
              {sources.map((source) => {
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
                  <span key={source.id} title={kind ?? undefined} className="text-grey-body">
                    {label}
                  </span>
                );
              })}
            </p>
          </details>
        ) : null}
        {message.steps.length > 0 ? (
          <details>
            <summary className="cursor-pointer font-bold text-grey-body hover:text-ink">
              {checkedWords(message.steps.length)}
            </summary>
            <Steps steps={message.steps} />
          </details>
        ) : null}
        {!investigated && !failed ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => onInvestigate(message.question)}
            className="flex w-fit items-center gap-1.5 font-bold text-ink hover:text-grey-body disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Telescope size={13} aria-hidden />
            {INVESTIGATE_THIS}
          </button>
        ) : null}
      </div>
    </li>
  );
}

/** An answer as a reader takes it in: the verdict in bold, the drivers as a list. */
function AnswerText({ text }: { text: string }) {
  const { verdict, bullets, rest, notKnown } = answerParts(text);
  return (
    <>
      {verdict ? <p className="font-bold">{verdict}</p> : null}
      {bullets.length > 0 ? (
        <ul className={cn("grid gap-1.5", verdict && "mt-1.5")}>
          {bullets.map((bullet) => (
            <li key={bullet} className="flex gap-2">
              <span aria-hidden className="text-grey-secondary">
                •
              </span>
              <span>{bullet}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {rest.map((line) => (
        <p key={line} className="mt-1.5">
          {line}
        </p>
      ))}
      {notKnown ? (
        <p className="mt-2 text-[13px] text-grey-body">
          <span className="font-bold">Not known:</span> {notKnown}
        </p>
      ) : null}
    </>
  );
}

/**
 * An investigation's steps: each question with how it went. `brief` while it
 * runs, with the questions only; once answered, each with what it found, why
 * it found nothing, and the tools it read with.
 */
function Steps({ steps, brief = false }: { steps: InvestigateStepResponse[]; brief?: boolean }) {
  return (
    <ol aria-label="Steps" className="mt-2 grid gap-2">
      {steps.map((step) => (
        <li key={step.index} className="flex gap-2 text-[12.5px] leading-snug">
          <span className="mt-px flex-none">
            {step.status === "running" ? (
              <LoaderCircle
                size={14}
                className="animate-spin text-grey-secondary"
                aria-label="Checking"
              />
            ) : step.status === "done" ? (
              <Check size={14} className="text-ink" aria-label="Checked" />
            ) : (
              <X size={14} className="text-rag-red" aria-label="Not checked" />
            )}
          </span>
          <div className="min-w-0">
            <p className={cn("font-bold", brief ? "text-grey-body" : "text-ink")}>
              {step.question}
            </p>
            {!brief && step.findings.length > 0 ? (
              <ul className="mt-0.5 grid gap-0.5 text-grey-body">
                {step.findings.map((finding) => (
                  <li key={finding}>• {finding}</li>
                ))}
              </ul>
            ) : null}
            {!brief && step.error ? <p className="mt-0.5 text-rag-red">{step.error}</p> : null}
            {!brief && step.tools_used.length > 0 ? (
              <p className="mt-0.5 text-grey-secondary">Read: {toolWords(step.tools_used)}</p>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
