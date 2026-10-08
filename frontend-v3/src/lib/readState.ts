/*
 * What a panel needs to know of the read behind it, so that it says "nothing
 * here" only once the read has answered. No runtime imports, so `node --test`
 * can run it.
 */

/** The part of a TanStack Query result a panel goes by; a real result has more. */
export type Read = {
  /** Fetching now, for the first time. False for a query held back by `enabled: false`. */
  isLoading: boolean;
  /** No data yet and no error: fetching, or held back. */
  isPending: boolean;
  error: unknown;
};

export type ReadState = { isLoading: boolean; error: unknown };

/**
 * `isLoading` and `error` for a panel fed by `read`, to pass to `PanelState`.
 *
 * A query held back with `enabled: false` is pending but never "loading", so a
 * panel that went by `read.isLoading` alone drew its empty state, from data
 * that was merely `undefined`, until the read it hangs off had answered (the
 * program id every portfolio read asks with). Name those reads in `after`:
 *
 * - the panel is loading while `read` is fetching, and while any read in
 *   `after` has not answered;
 * - a read in `after` that failed is the panel's error, since `read` never
 *   ran and an empty state would say it came back empty;
 * - `read`'s own error wins.
 *
 * Only reads that really are asked belong in `after`: one that is itself held
 * back for good (a role that may not open it) stays pending forever. A read
 * held back for any other reason, a role that may not ask or nothing to ask
 * about, is not waited for: the panel says why, with `locked` or its empty text.
 */
export function readState(read: Read, ...after: Read[]): ReadState {
  return {
    isLoading: read.isLoading || after.some((parent) => parent.isPending),
    error: read.error ?? after.find((parent) => parent.error)?.error ?? null,
  };
}
