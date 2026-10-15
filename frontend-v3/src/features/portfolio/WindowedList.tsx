import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { visibleRange } from "./windowing";

/** Past this many items only the ones near the screen are drawn. */
export const WINDOW_FROM = 100;

/**
 * A long list drawn only where it is seen: up to `WINDOW_FROM` items it is just
 * the list; past that, the items near the window are drawn between two spacers
 * holding the rest's height, measured once drawn and estimated before. The page
 * scrolls as one; nothing scrolls inside it.
 */
export function WindowedList<T>({
  items,
  keyOf,
  render,
  estimate = 320,
}: {
  items: readonly T[];
  keyOf: (item: T) => string;
  render: (item: T) => ReactNode;
  /** The height an item is taken to have before it was drawn, in pixels. */
  estimate?: number;
}) {
  if (items.length <= WINDOW_FROM) {
    return (
      <>
        {items.map((item) => (
          <Fragment key={keyOf(item)}>{render(item)}</Fragment>
        ))}
      </>
    );
  }
  return <Windowed items={items} keyOf={keyOf} render={render} estimate={estimate} />;
}

function Windowed<T>({
  items,
  keyOf,
  render,
  estimate,
}: {
  items: readonly T[];
  keyOf: (item: T) => string;
  render: (item: T) => ReactNode;
  estimate: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const heights = useRef(new Map<string, number>());
  const [version, setVersion] = useState(0);
  const [viewport, setViewport] = useState({ top: 0, height: 900 });

  const measure = useCallback(() => {
    const element = box.current;
    if (!element) return;
    const top = -element.getBoundingClientRect().top;
    setViewport((current) =>
      Math.abs(current.top - top) < 40 && current.height === window.innerHeight
        ? current
        : { top, height: window.innerHeight },
    );
  }, []);

  useLayoutEffect(measure, [measure, items.length]);
  useEffect(() => {
    window.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, [measure]);

  const sizes = items.map((item) => heights.current.get(keyOf(item)) ?? estimate);
  const range = visibleRange(sizes, viewport.top, viewport.height, 900);
  // `version` is read so a new measurement redraws with the real heights.
  void version;

  const onSize = useCallback((key: string, height: number) => {
    if (heights.current.get(key) === height) return;
    heights.current.set(key, height);
    setVersion((value) => value + 1);
  }, []);

  return (
    <div ref={box} style={{ paddingTop: range.before, paddingBottom: range.after }}>
      {items.slice(range.first, range.last + 1).map((item) => {
        const key = keyOf(item);
        return (
          <Measured key={key} id={key} onSize={onSize}>
            {render(item)}
          </Measured>
        );
      })}
    </div>
  );
}

function Measured({
  id,
  onSize,
  children,
}: {
  id: string;
  onSize: (key: string, height: number) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const report = () => onSize(id, element.getBoundingClientRect().height);
    report();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(report);
    observer.observe(element);
    return () => observer.disconnect();
  }, [id, onSize]);
  return <div ref={ref}>{children}</div>;
}
