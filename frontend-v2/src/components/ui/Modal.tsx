import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

export function Modal({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Content className="animate-op-pop fixed left-1/2 top-1/2 z-50 w-[520px] max-w-[90vw] -translate-x-1/2 -translate-y-1/2 rounded-3xl bg-white p-7 shadow-op-palette">
          <Dialog.Title className="text-[20px] font-bold">{title}</Dialog.Title>
          <div className="mt-4">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
