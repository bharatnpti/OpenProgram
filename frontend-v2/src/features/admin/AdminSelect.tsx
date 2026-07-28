import { type SelectHTMLAttributes, forwardRef } from "react";

import { cn } from "../../lib/utils";
import type { ConfigNodeResponse } from "../../api/schema";

export const AdminSelect = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function AdminSelect({ className, children, ...rest }, ref) {
    return (
      <select
        ref={ref}
        className={cn(
          "w-full rounded-2xl border border-grey-border bg-white px-4 py-3 text-[15px] outline-none focus:border-ink",
          className,
        )}
        {...rest}
      >
        {children}
      </select>
    );
  },
);

export function NodeSelect({
  items,
  value,
  onChange,
  placeholder,
  id,
}: {
  items: ConfigNodeResponse[];
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  id?: string;
}) {
  return (
    <AdminSelect id={id} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {items.map((item) => (
        <option key={item.id} value={item.id}>
          {item.name}
        </option>
      ))}
    </AdminSelect>
  );
}
