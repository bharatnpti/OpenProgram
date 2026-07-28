import { type InputHTMLAttributes, type TextareaHTMLAttributes, forwardRef } from "react";

import { cn } from "../../lib/utils";

const fieldClasses =
  "w-full rounded-2xl border border-grey-border bg-white px-4 py-3 text-[15px] outline-none focus:border-ink";

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput({ className, ...rest }, ref) {
    return <input ref={ref} className={cn(fieldClasses, className)} {...rest} />;
  },
);

export const TextArea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function TextArea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cn(fieldClasses, "min-h-[96px]", className)} {...rest} />;
});
