import * as Dialog from "@radix-ui/react-dialog";
import { useRef, type ReactNode } from "react";

import { cn } from "../../lib/utils";

export const field = "h-10 w-full rounded-xl border border-grey-border bg-white px-3 text-[14px]";
export const fieldLabel =
  "mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary";

/**
 * The frame every Reports dialog shares: title, one line on what saving does,
 * and the form. Controlled, so a dialog opened from a table row closes itself
 * when its change is saved. These dialogs open from plain buttons, with no
 * Dialog.Trigger for Radix to return focus to, so the control that had focus
 * when one opened gets it back when it closes; without that, focus fell to the
 * page and the next Tab started at the top.
 */
export function ReportDialog({
  open,
  onOpenChange,
  title,
  description,
  wide = false,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  wide?: boolean;
  children: ReactNode;
}) {
  const returnTo = useRef<HTMLElement | null>(null);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          onOpenAutoFocus={() => {
            const focused = document.activeElement;
            returnTo.current = focused instanceof HTMLElement ? focused : null;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            returnTo.current?.focus();
          }}
          className={cn(
            "fixed left-1/2 top-1/2 z-50 max-h-[90vh] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop",
            wide ? "w-[min(94vw,760px)]" : "w-[min(92vw,520px)]",
          )}
        >
          <Dialog.Title className="text-[20px] font-extrabold text-balance">{title}</Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] text-grey-body">
            {description}
          </Dialog.Description>
          <div className="mt-4">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Why a control is off just now (a past day is shown), where the control would be. */
export function Locked({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("text-[12px] text-grey-secondary", className)}>{children}</p>;
}

/** A form's own complaint, said before the server is asked. */
export function FormProblem({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="text-[13px] font-bold text-rag-red">
      {children}
    </p>
  );
}
