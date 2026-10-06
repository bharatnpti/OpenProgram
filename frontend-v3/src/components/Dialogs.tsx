import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { useState, type ReactNode } from "react";

import { Pill } from "./ui/Pill";

const overlay = "fixed inset-0 z-40 bg-black/30";
const panel =
  "fixed left-1/2 top-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop";

/** A yes/no step before an action that cannot be taken back, such as sending a report. */
export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel,
  onConfirm,
}: {
  trigger: ReactNode;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog.Root>
      <AlertDialog.Trigger asChild>{trigger}</AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className={overlay} />
        <AlertDialog.Content className={panel}>
          <AlertDialog.Title className="text-[20px] font-extrabold">{title}</AlertDialog.Title>
          <AlertDialog.Description className="mt-2 text-[14px] text-grey-body">
            {description}
          </AlertDialog.Description>
          <div className="mt-6 flex justify-end gap-2">
            <AlertDialog.Cancel asChild>
              <Pill variant="ghost" size="sm">
                Cancel
              </Pill>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Pill size="sm" onClick={onConfirm}>
                {confirmLabel}
              </Pill>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

/** One short piece of text, such as the day's note, edited in a dialog. */
export function TextDialog({
  trigger,
  title,
  description,
  initial,
  saveLabel,
  saving,
  onSave,
}: {
  trigger: ReactNode;
  title: string;
  description: string;
  initial: string;
  saveLabel: string;
  saving: boolean;
  onSave: (text: string) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(initial);

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setText(initial);
      }}
    >
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className={overlay} />
        <Dialog.Content className={panel}>
          <Dialog.Title className="text-[20px] font-extrabold">{title}</Dialog.Title>
          <Dialog.Description className="mt-2 text-[14px] text-grey-body">
            {description}
          </Dialog.Description>
          <form
            className="mt-4"
            onSubmit={(event) => {
              event.preventDefault();
              void onSave(text.trim()).then(() => setOpen(false));
            }}
          >
            <label htmlFor="text-dialog-input" className="sr-only">
              {title}
            </label>
            <textarea
              id="text-dialog-input"
              className="min-h-32 w-full rounded-2xl border border-grey-border p-3 text-[14px] focus:border-ink"
              value={text}
              onChange={(event) => setText(event.target.value)}
              maxLength={2000}
            />
            <div className="mt-4 flex justify-end gap-2">
              <Dialog.Close asChild>
                <Pill type="button" variant="ghost" size="sm">
                  Cancel
                </Pill>
              </Dialog.Close>
              <Pill type="submit" size="sm" disabled={saving}>
                {saving ? "Saving…" : saveLabel}
              </Pill>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
