import * as Dialog from "@radix-ui/react-dialog";
import { AppWindow, Maximize2, MessageCircleMore, Minimize2, SquarePen, X } from "lucide-react";
import { useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { cn } from "../../lib/utils";
import { useAssistant } from "./assistantContext";
import { ChatView } from "./ChatView";
import { ASK_LABEL, ASSISTANT_NAME, NEW_CHAT_LABEL, OPEN_PAGE_LABEL } from "./persona";

const PHONE = "(max-width: 639px)";
/** Ora's own page: the conversation in the whole tab. */
export const ORA_PATH = "/ora";

/**
 * The assistant, on every tab for the roles that may ask: a round button at
 * the bottom right that opens a chat panel (its conversation is ChatView.tsx).
 * The panel's header starts a new chat, opens Ora's page in this tab with the
 * same conversation, makes the panel full height, and closes it. The panel is
 * not modal: the page stays usable beside it. Esc closes it and focus goes
 * back to the button; on a phone it fills the screen. On Ora's own page there
 * is no button. Motion stops for prefers-reduced-motion (index.css).
 */
export function Assistant() {
  const assistant = useAssistant();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const [tall, setTall] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  if (!assistant.available || pathname === ORA_PATH) return null;

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
            "sm:inset-auto sm:bottom-24 sm:right-6 sm:w-[min(440px,calc(100vw-3rem))] sm:rounded-3xl sm:border sm:border-grey-border sm:shadow-op-palette",
            tall ? "sm:top-4" : "sm:h-[min(680px,calc(100dvh-8rem))]",
          )}
        >
          <header className="flex flex-none items-center gap-1 border-b border-grey-border px-4 py-3">
            <span
              aria-hidden
              className="mr-1 flex h-9 w-9 flex-none items-center justify-center rounded-full bg-ink text-white"
            >
              <MessageCircleMore size={18} />
            </span>
            <Dialog.Title className="min-w-0 flex-1 leading-tight">
              <span className="block text-[16px] font-extrabold">{ASSISTANT_NAME}</span>
              <span className="block text-[11px] font-medium text-grey-secondary">
                Asks the delivery data for you
              </span>
            </Dialog.Title>
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
            <HeaderButton
              label={OPEN_PAGE_LABEL}
              onClick={() => {
                assistant.setOpen(false);
                navigate(ORA_PATH, { state: { from: `${pathname}${search}` } });
              }}
            >
              <AppWindow size={17} aria-hidden />
            </HeaderButton>
            <HeaderButton
              label={tall ? "Back to a smaller panel" : "Expand to full height"}
              pressed={tall}
              onClick={() => setTall((on) => !on)}
              className="hidden sm:flex"
            >
              {tall ? <Minimize2 size={17} aria-hidden /> : <Maximize2 size={17} aria-hidden />}
            </HeaderButton>
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
          <ChatView
            variant="panel"
            inputRef={input}
            onLink={() => {
              // On a phone the panel covers the page the link opens.
              if (window.matchMedia?.(PHONE).matches) assistant.setOpen(false);
            }}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function HeaderButton({
  label,
  onClick,
  children,
  disabled = false,
  pressed,
  className,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  pressed?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex h-9 w-9 flex-none items-center justify-center rounded-full text-grey-body hover:bg-grey-fill disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
    >
      {children}
    </button>
  );
}
