import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

/* Small pieces the admin tabs share: field styles, a dialog, problems, an intro. */

export const inputClass =
  "h-10 w-full min-w-0 rounded-xl border border-grey-border bg-white px-3 text-[14px]";
export const labelClass =
  "mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary";
export const hintClass = "mt-1 text-[12px] text-grey-secondary";

/** What stops a save, listed where the Save button is. */
export function Problems({ problems }: { problems: string[] }) {
  if (problems.length === 0) return null;
  return (
    <ul
      role="alert"
      className="grid gap-1 rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red"
    >
      {problems.map((problem) => (
        <li key={problem}>{problem}</li>
      ))}
    </ul>
  );
}

/** A dialog the caller opens and closes; wide for editors with tables. */
export function AdminDialog({
  open,
  onOpenChange,
  title,
  description,
  wide,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          // Radix links the description itself; with none, say so to keep it quiet.
          {...(description ? {} : { "aria-describedby": undefined })}
          className={cn(
            "fixed left-1/2 top-1/2 z-50 max-h-[90vh] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop",
            wide ? "w-[min(94vw,760px)]" : "w-[min(94vw,560px)]",
          )}
        >
          <Dialog.Title className="text-[20px] font-extrabold">{title}</Dialog.Title>
          {description ? (
            <Dialog.Description className="mt-1 text-[13px] text-grey-body">
              {description}
            </Dialog.Description>
          ) : null}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** An intro paragraph for a tab: what it is for, in plain words. */
export function TabIntro({ children }: { children: ReactNode }) {
  return <p className="max-w-[760px] text-[14px] text-grey-body">{children}</p>;
}
