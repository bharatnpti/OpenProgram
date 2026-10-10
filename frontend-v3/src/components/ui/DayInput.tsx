import type { MouseEvent, ReactNode } from "react";

import { cn } from "../../lib/utils";
import { dayFieldWords, localIso } from "../../lib/viewingDate";

/**
 * A day field that reads the way every other screen writes a day ("Wed 7 Oct"),
 * not in the browser's own order ("07/10/2026" here, "10/07/2026" there), so one
 * screen never shows two styles.
 *
 * The browser's date input stays: it lies over the face, invisible and the size
 * of it, so the keyboard, a screen reader and a phone's own picker work as they
 * do for any date field, and a click opens the browser's calendar. `className`
 * draws the face (its border, height, colour); give the field a name with
 * `aria-label`, or a <label htmlFor={id}>.
 */
export function DayInput({
  id,
  value,
  onChange,
  min,
  max,
  icon,
  placeholder = "Pick a day",
  className,
  "aria-label": ariaLabel,
}: {
  id?: string;
  /** An ISO day (YYYY-MM-DD), or "" for none. */
  value: string;
  /** The value as the browser gives it: "" while a day is half typed or cleared. */
  onChange: (value: string) => void;
  min?: string;
  max?: string;
  icon?: ReactNode;
  placeholder?: string;
  className?: string;
  "aria-label"?: string;
}) {
  const words = dayFieldWords(value, localIso());
  return (
    <span className="relative block min-w-0">
      <input
        id={id}
        type="date"
        aria-label={ariaLabel}
        className="peer absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0"
        value={value}
        min={min}
        max={max}
        onChange={(event) => onChange(event.target.value)}
        onClick={openPicker}
      />
      <span
        aria-hidden
        className={cn(
          "flex items-center peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-magenta",
          className,
          words === null && "text-grey-secondary",
        )}
      >
        {icon}
        <span className="truncate">{words ?? placeholder}</span>
      </span>
    </span>
  );
}

/** A click anywhere on the field opens the calendar, as it does on the browser's own icon. */
function openPicker(event: MouseEvent<HTMLInputElement>) {
  try {
    event.currentTarget.showPicker();
  } catch {
    // No picker here (an older browser, or one already open): the field still takes typing.
  }
}
