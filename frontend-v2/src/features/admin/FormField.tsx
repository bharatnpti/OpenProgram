import type { ReactNode } from "react";

export function FormField({
  label,
  htmlFor,
  error,
  children,
}: {
  label: string;
  htmlFor?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="text-[13px] font-bold text-grey-secondary">
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
      {error ? <p className="mt-1 text-[12px] font-medium text-rag-red">{error}</p> : null}
    </div>
  );
}
