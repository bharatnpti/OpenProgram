import { X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { refusal } from "./adminErrors";
import { fieldInput } from "./AdminDialog";

/** One thing a parent is linked to, with a button to take it away. */
export function Chip({
  label,
  note,
  removeLabel,
  onRemove,
  disabled,
}: {
  label: string;
  note?: string;
  /** What the remove button says to a screen reader: "Take Kai Thompson out of Payments Pod". */
  removeLabel: string;
  onRemove: () => void;
  disabled?: boolean;
}) {
  return (
    <li className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-grey-border bg-white py-1 pl-3 pr-1 text-[13px]">
      <span className="truncate font-bold">{label}</span>
      {note ? <span className="truncate text-grey-secondary">· {note}</span> : null}
      <button
        type="button"
        aria-label={removeLabel}
        title={removeLabel}
        disabled={disabled}
        onClick={onRemove}
        className="grid h-6 w-6 flex-none place-items-center rounded-full text-grey-secondary hover:bg-rag-red-bg hover:text-rag-red disabled:opacity-50"
      >
        <X size={14} aria-hidden />
      </button>
    </li>
  );
}

/** The chips linked to a parent, or a line saying there are none. */
export function ChipList({ items, empty }: { items: ReactNode[]; empty: string }) {
  return items.length > 0 ? (
    <ul className="flex flex-wrap gap-1.5">{items}</ul>
  ) : (
    <p className="text-[13px] text-grey-secondary">{empty}</p>
  );
}

export type Option = { value: string; label: string; disabled?: boolean };

/** A select with a label a screen reader can find; the placeholder is its first, empty option. */
export function SelectField({
  id,
  label,
  placeholder,
  value,
  onChange,
  options,
  className,
}: {
  id: string;
  label: string;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  className?: string;
}) {
  return (
    <>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <select
        id={id}
        className={cn(fieldInput, "w-auto min-w-0 max-w-full", className)}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
    </>
  );
}

/**
 * Pick one thing and add it. A refusal from the server is shown as a toast in words and the
 * pick stays, so the person can change it and try again.
 */
export function AddControl({
  id,
  label,
  placeholder,
  nothingLeft,
  options,
  onAdd,
  buttonLabel = "Add",
  children,
  onSelect,
}: {
  id: string;
  label: string;
  placeholder: string;
  /** Said instead of the placeholder when every option is already linked. */
  nothingLeft: string;
  options: Option[];
  /** Links the pick; throws when the server refuses. */
  onAdd: (value: string) => Promise<void>;
  buttonLabel?: string;
  /** More controls that belong to the same pick, such as a role. */
  children?: (selected: string) => ReactNode;
  /** Told which option was picked, so a control beside the select can follow it. */
  onSelect?: (value: string) => void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const available = options.some((option) => !option.disabled);

  const add = async () => {
    setBusy(true);
    try {
      await onAdd(value);
      setValue("");
    } catch (error) {
      toast.error(refusal(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (value && !busy) void add();
      }}
    >
      <SelectField
        id={id}
        label={label}
        placeholder={available ? placeholder : nothingLeft}
        value={value}
        onChange={(next) => {
          setValue(next);
          if (next) onSelect?.(next);
        }}
        options={options}
      />
      {children?.(value)}
      <Pill type="submit" size="sm" variant="dark" disabled={!value || busy}>
        {busy ? "Adding…" : buttonLabel}
      </Pill>
    </form>
  );
}
