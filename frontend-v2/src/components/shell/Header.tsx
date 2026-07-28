import { NavLink } from "react-router-dom";
import { Search } from "lucide-react";

import { cn } from "../../lib/utils";
import { AvatarMenu } from "./AvatarMenu";

const NAV_ITEMS = [
  { label: "Today", to: "/today" },
  { label: "Delivery", to: "/delivery" },
  { label: "Signals", to: "/signals" },
  { label: "Coordination", to: "/coordination" },
];

export function Header({ onOpenPalette }: { onOpenPalette: () => void }) {
  return (
    <header className="sticky top-0 z-40 flex h-[72px] items-center gap-4 border-b border-grey-border bg-white/94 px-6 backdrop-blur-sm">
      <div className="flex shrink-0 items-center gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-lg bg-magenta text-[22px] font-extrabold text-white">
          T
        </div>
        <div>
          <div className="text-[18px] font-extrabold tracking-tight">OpenProgram</div>
          <div className="text-xs text-grey-secondary">Delivery intelligence</div>
        </div>
      </div>

      <nav aria-label="Primary" className="flex h-full items-center gap-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "relative flex h-full items-center px-[18px] text-[16px] no-underline",
                isActive ? "font-bold text-ink" : "font-medium text-grey-secondary hover:text-ink",
              )
            }
          >
            {({ isActive }) => (
              <>
                {item.label}
                <span
                  className={cn(
                    "absolute inset-x-3.5 bottom-0 h-[3px] rounded-t-sm",
                    isActive ? "bg-magenta" : "bg-transparent",
                  )}
                />
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="ml-auto flex min-w-0 flex-none items-center gap-3">
        <button
          type="button"
          onClick={onOpenPalette}
          className="flex h-12 min-w-0 flex-[0_1_260px] items-center gap-2.5 rounded-full border border-grey-border bg-grey-fill px-1 pl-[18px] text-sm text-grey-secondary hover:border-grey-disabled"
        >
          <Search size={16} />
          <span className="flex-1 truncate text-left">Search or jump to…</span>
          <span className="shrink-0 rounded-md border border-grey-border bg-white px-2 py-0.5 text-xs text-grey-secondary">
            ⌘K
          </span>
        </button>
        <AvatarMenu />
      </div>
    </header>
  );
}
