// No imports: this module runs under `node --test` as written.

/**
 * Whether an ask the viewer raised is still theirs to watch. One whose name was
 * not matched to anyone ("needs resolution") waits on the requester's own
 * answer, so it stays on their list; only a resolved or dismissed one leaves.
 */
export function raisedStillOpen(status: string): boolean {
  return status === "open" || status === "acknowledged" || status === "needs_resolution";
}
