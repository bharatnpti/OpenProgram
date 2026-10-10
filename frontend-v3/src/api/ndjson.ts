// Newline-delimited JSON, read as it arrives: POST /ask/investigate sends one
// object per line while it works. Pure, so `node --test` runs it as written.

/** The whole lines in `buffer`, and what is left after the last newline. */
export function splitLines(buffer: string): { lines: string[]; rest: string } {
  const parts = buffer.split("\n");
  const rest = parts.pop() ?? "";
  return { lines: parts.map((line) => line.trim()).filter(Boolean), rest };
}

/**
 * Each line of `body` parsed and handed to `onLine` as it comes, the last one
 * too when the stream ends without a newline. A line that is not JSON throws:
 * the server sends only JSON, so anything else means the stream is broken.
 */
export async function readNdjson(
  body: ReadableStream<Uint8Array>,
  onLine: (value: unknown) => void,
): Promise<void> {
  const reader = body.getReader();
  // One decoder for the whole stream: a character split across chunks decodes whole.
  const decoder = new TextDecoder();
  let rest = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const split = splitLines(rest + decoder.decode(value, { stream: true }));
    rest = split.rest;
    split.lines.forEach((line) => onLine(JSON.parse(line)));
  }
  rest += decoder.decode();
  if (rest.trim()) onLine(JSON.parse(rest));
}
