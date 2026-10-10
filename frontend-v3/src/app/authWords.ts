// What went wrong when the console asked the backend who is signed in. No
// runtime imports, so `node --test` can run it.

/**
 * One sentence on why `/api/v1/auth/status` gave no usable answer.
 *
 * A refused or failed fetch (the backend is stopped, on another address, or
 * does not list this console's address for CORS) never reaches the backend's
 * error handling, so it has no status. An HTTP error has one, and the
 * backend's own message.
 */
export function describeAuthFailure(error: unknown): string {
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = (error as { status: unknown }).status;
    const message = error instanceof Error ? error.message : "";
    if (typeof status === "number") {
      return `It answered with an error (${status})${message ? `: ${message}` : "."}`;
    }
  }
  return (
    "It did not answer. The backend may be stopped or still starting, run on another " +
    "address, or not allow this console's address (CORS)."
  );
}
