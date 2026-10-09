import { useState, type ReactNode } from "react";

import { cn } from "../../lib/utils";

/**
 * A native disclosure whose contents render only once it is first opened, so a
 * folded table of every requirement, or Daily's past sends, costs nothing until
 * someone asks for it. The summary is a pill with + or −, 44 px tall on a phone.
 * Its greys are the theme's utilities, so the dark remap inside a page reaches them.
 */
export function Fold({
  summary,
  children,
  className,
}: {
  summary: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const [opened, setOpened] = useState(false);
  return (
    <details
      className={cn("group", className)}
      onToggle={(event) => {
        if ((event.currentTarget as HTMLDetailsElement).open) setOpened(true);
      }}
    >
      <summary className="inline-flex min-h-9 cursor-pointer list-none items-center gap-2 rounded-full border border-grey-border px-3.5 text-[13px] font-bold max-sm:min-h-11 [&::-webkit-details-marker]:hidden">
        <span
          aria-hidden
          className="inline-grid h-[18px] w-[18px] place-items-center rounded-full bg-grey-fill font-extrabold leading-none"
        >
          <span className="group-open:hidden">+</span>
          <span className="hidden group-open:inline">−</span>
        </span>
        {summary}
      </summary>
      {opened ? <div className="mt-3">{children}</div> : null}
    </details>
  );
}
