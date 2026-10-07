import { useEffect, useState } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";

/** Whether the device asks for less motion: the flow band is then a still picture. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.(REDUCED).matches === true,
  );
  useEffect(() => {
    const query = window.matchMedia?.(REDUCED);
    if (!query) return;
    const change = () => setReduced(query.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  return reduced;
}
