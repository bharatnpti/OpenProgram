import { Fragment, useEffect, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { projectAnchors, type ProjectSection } from "../project/sections";

/**
 * A tab's sections, top to bottom, with the in-page index above them once
 * three or more are drawn. A link to a section (`#gates`, `#work`…) lands on
 * it once it is drawn: the sections read their data first. The timeline lands
 * by itself.
 */
export function SectionList({ drawn }: { drawn: { key: ProjectSection; element: ReactNode }[] }) {
  const anchors = projectAnchors(drawn.map(({ key }) => key));
  useHashLanding(drawn.length > 0);
  return (
    <>
      {anchors.length > 0 ? (
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
        {drawn.map(({ key, element }) => (
          <Fragment key={key}>{element}</Fragment>
        ))}
      </div>
    </>
  );
}

/** An anchored section, so the index and a sent link can land on it. */
export function Anchored({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div id={id} className="scroll-mt-28">
      {children}
    </div>
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
