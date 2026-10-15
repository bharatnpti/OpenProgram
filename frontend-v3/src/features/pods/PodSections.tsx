import { Fragment, useEffect, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

/**
 * One section of a pod's tab: its anchor, its word in the index (none: not
 * listed), and what it draws. `ownsId`: the element carries the anchor itself
 * (the timeline, readiness, hygiene), so it is not wrapped in a second one.
 */
export type PodSection = {
  id: string;
  label: string | null;
  element: ReactNode;
  ownsId?: boolean;
};

/**
 * A pod's tab, its sections top to bottom, with the in-page index above them
 * once three or more with a word are drawn, as a project's tabs have. A link to
 * a section (`#checkins`, `#blockers`, `#status`…, the old Pod page's anchors)
 * lands on it once it is drawn: the sections read their data first.
 */
export function PodSections({ sections }: { sections: PodSection[] }) {
  const anchors = sections.filter((section) => section.label !== null);
  useHashLanding(sections.length > 0);
  return (
    <>
      {anchors.length >= 3 ? (
        <nav
          aria-label="On this tab"
          className="sticky z-20 -mx-1 mb-6 flex gap-1 overflow-x-auto bg-(--op-viz-surface) px-1 py-2 [scrollbar-width:none] max-md:[mask-image:linear-gradient(to_right,black_calc(100%-28px),transparent)]"
          style={{ top: "var(--op-header-h, 64px)" }}
        >
          {anchors.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className="flex-none rounded-full border border-grey-border px-3 py-1.5 text-[13px] font-bold text-grey-body no-underline hover:bg-grey-fill hover:text-ink"
            >
              {item.label}
            </a>
          ))}
        </nav>
      ) : null}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-10 max-sm:gap-8">
        {sections.map(({ id, element, ownsId }) =>
          ownsId ? (
            <Fragment key={id}>{element}</Fragment>
          ) : (
            <div key={id} id={id} className="min-w-0 scroll-mt-28">
              {element}
            </div>
          ),
        )}
      </div>
    </>
  );
}

function useHashLanding(ready: boolean) {
  const { hash } = useLocation();
  useEffect(() => {
    const id = decodeURIComponent(hash.slice(1));
    if (!ready || !id || id === "timeline") return;
    let tries = 0;
    const timer = window.setInterval(() => {
      const element = document.getElementById(id);
      tries += 1;
      if (element || tries > 25) {
        window.clearInterval(timer);
        element?.scrollIntoView({ block: "start" });
      }
    }, 200);
    return () => window.clearInterval(timer);
  }, [hash, ready]);
}
