import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { useState, type ReactNode } from "react";

import { Pill } from "../../components/ui/Pill";
import { refusal } from "./adminErrors";

const overlay = "fixed inset-0 z-40 bg-black/30";
const panel =
  "fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop";

/** A dialog for a form: a title, one line on what it does, then the form. */
export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={overlay} />
        <Dialog.Content className={panel}>
          <Dialog.Title className="text-[20px] font-extrabold">{title}</Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] text-grey-body">
            {description}
          </Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** A change waiting for a yes: what it is, what else it affects, and how to carry it out. */
export type Pending = {
  /** A new change gets a new key, so the dialog starts clean. */
  key: string;
  title: string;
  /** The one sentence on what happens. */
  effect?: string;
  /** What else changes, one line each. */
  lines?: string[];
  /** Consequences to take seriously; the person may still go ahead. */
  warnings?: string[];
  /** The people or things the change is about, each named, in a list the person can read. */
  names?: string[];
  confirmLabel: string;
  /** Carries the change out; a refusal stays in the dialog, in words, so it can be read. */
  run: () => Promise<unknown>;
  /** Everything but a removal is not destructive; the button is red only for one. */
  destructive?: boolean;
};

/**
 * The yes/no step before a change that cannot be taken back or that moves a lot with it. It
 * says what will happen before anything does, and a refusal from the server stays on screen.
 */
export function ConfirmChange({
  pending,
  onClose,
}: {
  pending: Pending | null;
  onClose: () => void;
}) {
  return (
    <AlertDialog.Root
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <AlertDialog.Portal>
        <AlertDialog.Overlay className={overlay} />
        <AlertDialog.Content className={panel}>
          {pending ? <ConfirmBody key={pending.key} pending={pending} onClose={onClose} /> : null}
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

function ConfirmBody({ pending, onClose }: { pending: Pending; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      await pending.run();
      onClose();
    } catch (e) {
      setError(refusal(e));
      setBusy(false);
    }
  };

  return (
    <>
      <AlertDialog.Title className="text-[20px] font-extrabold">{pending.title}</AlertDialog.Title>
      <AlertDialog.Description asChild>
        <div className="mt-2 grid gap-3 text-[14px] text-grey-body">
          {pending.effect ? <p>{pending.effect}</p> : null}
          {pending.names && pending.names.length > 0 ? (
            <ul className="max-h-48 list-none overflow-y-auto rounded-2xl bg-grey-fill px-4 py-2 text-ink">
              {pending.names.map((name) => (
                <li key={name} className="py-0.5 text-[14px] font-bold">
                  {name}
                </li>
              ))}
            </ul>
          ) : null}
          {pending.lines && pending.lines.length > 0 ? (
            <ul className="grid list-disc gap-1.5 pl-5">
              {pending.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
          {(pending.warnings ?? []).map((warning) => (
            <p key={warning} className="rounded-2xl bg-rag-amber-bg px-4 py-3 text-rag-amber-deep">
              {warning}
            </p>
          ))}
        </div>
      </AlertDialog.Description>
      {error ? (
        <p
          role="alert"
          className="mt-3 rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red"
        >
          {error}
        </p>
      ) : null}
      <div className="mt-6 flex justify-end gap-2">
        <AlertDialog.Cancel asChild>
          <Pill variant="ghost" size="sm" disabled={busy}>
            Cancel
          </Pill>
        </AlertDialog.Cancel>
        <Pill
          size="sm"
          disabled={busy}
          onClick={() => void go()}
          className={
            pending.destructive === false
              ? undefined
              : "bg-rag-red hover:bg-rag-red disabled:hover:bg-rag-red"
          }
        >
          {busy ? "Working…" : pending.confirmLabel}
        </Pill>
      </div>
    </>
  );
}

/** The red outline every removal button wears, so a destructive step looks like one. */
export const removeButtonClass =
  "border-rag-red text-rag-red hover:bg-rag-red-bg disabled:hover:bg-transparent";

/** Labelled fields, the way the other admin dialogs draw them. */
export const fieldInput =
  "h-10 w-full rounded-xl border border-grey-border bg-white px-3 text-[14px] focus:border-ink disabled:bg-grey-fill";
export const fieldLabel =
  "mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary";
export const fieldHelp = "mt-1 text-[12px] text-grey-secondary";
export const fieldError = "mt-1 text-[12px] font-bold text-rag-red";
