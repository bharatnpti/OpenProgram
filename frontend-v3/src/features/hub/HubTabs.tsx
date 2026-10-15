import { Link, useLocation } from "react-router-dom";

import { useRole } from "../../app/role";
import { cn } from "../../lib/utils";
import { HUB_TAB_LABELS, type HubPlace, type HubTab } from "./hubPaths";
import { hubHref, type Badge } from "./hubTabs";

/**
 * The four questions, as tabs: Will we make the date?, Early warnings, Scope
 * control and Reports, the ones the role has at this level. Each carries its
 * summary as a badge, so no Overview tab is needed. A tab keeps the place (the
 * pod stays), the viewing day and the release. On a phone the row scrolls
 * sideways inside itself, never the page.
 */
export function HubTabs({
  place,
  tab,
  badges,
}: {
  place: HubPlace;
  tab: HubTab;
  badges: Partial<Record<HubTab, Badge | null>>;
}) {
  const { access } = useRole();
  const { search } = useLocation();
  const tabs = access.hub.tabs[place.level];
  if (tabs.length <= 1) return null;
  return (
    <nav
      aria-label="Questions"
      className="-mx-1 mb-4 overflow-x-auto border-b border-grey-border px-1 [scrollbar-width:none] max-md:[mask-image:linear-gradient(to_right,black_calc(100%-28px),transparent)]"
    >
      <ul className="flex min-w-max gap-1">
        {tabs.map((item) => {
          const current = item === tab;
          const badge = badges[item] ?? null;
          return (
            <li key={item}>
              <Link
                to={hubHref(access.hub.tabs, { place, tab }, place, item, search)}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "relative -mb-px flex min-h-11 items-center gap-2 border-b-[3px] px-3 text-[14px] font-bold no-underline",
                  current
                    ? "border-magenta text-ink"
                    : "border-transparent text-grey-body hover:text-ink",
                )}
              >
                {HUB_TAB_LABELS[item]}
                {badge ? (
                  <span
                    className="rounded-full bg-grey-fill px-2 py-0.5 text-[12px] font-bold text-grey-body"
                    aria-label={badge.label}
                  >
                    <span aria-hidden>{badge.text}</span>
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
