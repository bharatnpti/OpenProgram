import type { ReactNode } from "react";

import { HUB_TAB_LABELS, type HubTab } from "./hubPaths";

/**
 * The top of a tab's content: the question it answers as its heading, for a
 * screen reader only (the tab bar says it on screen, so it is said once), one
 * line of what the tab shows, and the tab's own actions.
 */
export function TabIntro({
  tab,
  meta,
  actions,
}: {
  tab: HubTab;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 className="sr-only">{HUB_TAB_LABELS[tab]}</h2>
        {meta ? <p className="text-[13px] text-grey-secondary text-pretty">{meta}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}
