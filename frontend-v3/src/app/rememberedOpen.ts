// Whether a folding panel is open, remembered per person in localStorage. The
// storage helpers are pure and take the storage, so `node --test` runs them.
import { useState } from "react";

/** The few calls of localStorage these use, so a test can hand in its own. */
export type OpenStore = Pick<Storage, "getItem" | "setItem">;

/** The storage key a folding panel keeps its state under, per person: "…sm-checkins.U1006". */
export function rememberedKey(panel: string, who: string | null | undefined): string {
  return `openprogram.v3.open.${panel}.${who || "anyone"}`;
}

/** The stored choice, or `fallback` when there is none or storage is blocked. */
export function readRemembered(storage: () => OpenStore, key: string, fallback: boolean): boolean {
  try {
    const stored = storage().getItem(key);
    return stored === null ? fallback : stored === "1";
  } catch {
    // Private windows and blocked storage: the panel opens as it does by default.
    return fallback;
  }
}

/** Keep the choice; blocked storage only means it is not remembered. */
export function writeRemembered(storage: () => OpenStore, key: string, open: boolean): void {
  try {
    storage().setItem(key, open ? "1" : "0");
  } catch {
    // Private windows and blocked storage: the choice simply isn't remembered.
  }
}

const browser = () => window.localStorage;

/**
 * Whether a folding panel is open, remembered per person. Another person on
 * the same browser has their own choice: the state is read again when `who`
 * changes.
 */
export function useRememberedOpen(
  panel: string,
  who: string | null | undefined,
  fallback = false,
): [boolean, (open: boolean) => void] {
  const key = rememberedKey(panel, who);
  const [state, setState] = useState(() => ({
    key,
    open: readRemembered(browser, key, fallback),
  }));
  const open = state.key === key ? state.open : readRemembered(browser, key, fallback);
  const set = (next: boolean) => {
    setState({ key, open: next });
    writeRemembered(browser, key, next);
  };
  return [open, set];
}
