import { NavLink } from "react-router-dom";
import { Search } from "lucide-react";

import { useRole } from "../../app/role";
import { useBranding } from "../../features/branding/useBranding";
import { cn } from "../../lib/utils";
import { AvatarMenu } from "./AvatarMenu";
import { HeaderLogo } from "./HeaderLogo";
import { NavMenu, type NavItem } from "./NavMenu";
import { PersonPicker } from "./PersonPicker";
import { ViewingDateControl } from "./ViewingDate";

const NAV_ITEMS: NavItem[] = [
  { label: "Today", to: "/today" },
  { label: "Delivery", to: "/delivery" },
  { label: "Signals", to: "/signals", needsAggregate: true },
  { label: "Coordination", to: "/coordination" },
];

export function Header({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { chatEnabled, canReadAggregate } = useRole();
  const branding = useBranding();
  // Signals reads the whole portfolio, which a developer is not authorized for;
  // listing it would only lead to a screen of zeros. Chat is listed when served.
  const navItems: NavItem[] = [
    ...NAV_ITEMS.filter((item) => !item.needsAggregate || canReadAggregate),
    ...(chatEnabled ? [{ label: "Chat", to: "/chat" }] : []),
  ];
  return (
    <header className="sticky top-0 z-40 flex h-[72px] items-center gap-3 border-b border-grey-border bg-white/94 px-4 backdrop-blur-sm sm:gap-4 sm:px-6">
      <div className="flex shrink-0 items-center gap-3">
        <HeaderLogo logo={branding.data?.logo ?? null} loading={branding.isPending} />
        {/* The wordmark gives way below xl so the date control, person picker
            and avatar keep their room instead of pushing past the edge. */}
        <div className="hidden xl:block">
          <div className="text-[18px] font-extrabold tracking-tight">OpenProgram</div>
          <div className="text-xs text-grey-secondary">Delivery intelligence</div>
        </div>
      </div>

      {/* Below lg the pages fold into one menu, so the controls on the right
          never push past the edge on a tablet or a phone. */}
      <NavMenu items={navItems} onOpenPalette={onOpenPalette} />
      <nav aria-label="Primary" className="hidden h-full items-center gap-2 lg:flex">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "relative flex h-full items-center px-3 text-[16px] no-underline xl:px-[18px]",
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

      <div className="ml-auto flex min-w-0 flex-none items-center gap-2 sm:gap-3">
        <button
          type="button"
          onClick={onOpenPalette}
          className="hidden h-12 min-w-0 flex-[0_1_200px] items-center gap-2.5 rounded-full border border-grey-border bg-grey-fill px-1 pl-[18px] text-sm text-grey-secondary hover:border-grey-disabled xl:flex"
        >
          <Search size={16} />
          <span className="flex-1 truncate text-left">Search or jump to…</span>
          <span className="shrink-0 rounded-md border border-grey-border bg-white px-2 py-0.5 text-xs text-grey-secondary">
            ⌘K
          </span>
        </button>
        {/* Below sm the date lives in the menu; the banner says when it is past. */}
        <div className="hidden sm:block">
          <ViewingDateControl />
        </div>
        <PersonPicker />
        <AvatarMenu />
      </div>
    </header>
  );
}
