import * as AlertDialog from "@radix-ui/react-alert-dialog";

import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import type { ConfirmState } from "./adminTypes";

export function ConfirmDialog({
  state,
  onOpenChange,
}: {
  state: ConfirmState;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <AlertDialog.Root open={state.open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <AlertDialog.Content className="animate-op-pop fixed left-1/2 top-1/2 z-50 w-[420px] max-w-[90vw] -translate-x-1/2 -translate-y-1/2 rounded-3xl bg-white p-7 shadow-op-palette">
          <AlertDialog.Title className="text-[20px] font-bold">
            {state.open ? state.title : ""}
          </AlertDialog.Title>
          <AlertDialog.Description className="mt-2 text-[14px] text-grey-secondary">
            {state.open ? state.description : ""}
          </AlertDialog.Description>
          <div className="mt-6 flex justify-end gap-3">
            <AlertDialog.Cancel asChild>
              <Pill variant="ghost" size="md">
                Cancel
              </Pill>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Pill
                variant="primary"
                size="md"
                className={cn(state.open && state.destructive && "bg-rag-red hover:bg-rag-red")}
                onClick={() => {
                  if (state.open) state.onConfirm();
                  onOpenChange(false);
                }}
              >
                {state.open ? state.confirmLabel : "Confirm"}
              </Pill>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
