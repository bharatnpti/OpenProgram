import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { readState, type Read } from "./readState.ts";

// What TanStack Query reports in each state a panel can meet.
const fetching: Read = { isLoading: true, isPending: true, error: null };
const answered: Read = { isLoading: false, isPending: false, error: null };
const heldBack: Read = { isLoading: false, isPending: true, error: null };
const failed = (error: unknown): Read => ({ isLoading: false, isPending: false, error });

describe("readState", () => {
  test("a read that is being fetched is loading", () => {
    assert.deepEqual(readState(fetching), { isLoading: true, error: null });
  });

  test("a read that answered is neither loading nor failed", () => {
    assert.deepEqual(readState(answered), { isLoading: false, error: null });
  });

  test("a read held back until the read it hangs off answers is loading, not empty", () => {
    // The trend of a program is not asked while the programs load: pending, not loading.
    assert.equal(heldBack.isLoading, false);
    assert.deepEqual(readState(heldBack, fetching), { isLoading: true, error: null });
    assert.equal(readState(heldBack, heldBack).isLoading, true);
  });

  test("a read held back after the read it hangs off answered has nothing to ask: not loading", () => {
    // No program exists, so no trend is asked: the panel's own empty text is true.
    assert.deepEqual(readState(heldBack, answered), { isLoading: false, error: null });
  });

  test("a read held back for good, with nothing to wait for, is not loading", () => {
    assert.deepEqual(readState(heldBack), { isLoading: false, error: null });
  });

  test("waits for every read it hangs off", () => {
    assert.equal(readState(heldBack, answered, fetching).isLoading, true);
    assert.equal(readState(heldBack, answered, answered).isLoading, false);
  });

  test("a failed read it hangs off is its error: it never ran, so it is not empty", () => {
    const boom = new Error("programs are down");
    assert.deepEqual(readState(heldBack, failed(boom)), { isLoading: false, error: boom });
  });

  test("its own error wins over one it hangs off", () => {
    const own = new Error("trend is down");
    const parent = new Error("programs are down");
    assert.equal(readState(failed(own), failed(parent)).error, own);
  });

  test("the first failed read it hangs off is reported", () => {
    const first = new Error("first");
    const second = new Error("second");
    assert.equal(readState(heldBack, answered, failed(first), failed(second)).error, first);
  });

  test("still loading wins while another read it hangs off has failed", () => {
    const state = readState(heldBack, fetching, failed(new Error("x")));
    assert.equal(state.isLoading, true);
  });
});
