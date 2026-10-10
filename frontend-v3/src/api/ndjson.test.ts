import assert from "node:assert/strict";
import { test } from "node:test";

import { readNdjson, splitLines } from "./ndjson.ts";

const streamOf = (...chunks: string[]) =>
  new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });

test("whole lines are split off, and what follows the last newline waits", () => {
  assert.deepEqual(splitLines('{"a":1}\n\n{"b":2}\n{"c"'), {
    lines: ['{"a":1}', '{"b":2}'],
    rest: '{"c"',
  });
  assert.deepEqual(splitLines(""), { lines: [], rest: "" });
});

test("a line cut across chunks is read once it is whole, the unterminated last one too", async () => {
  const seen: unknown[] = [];
  await readNdjson(streamOf('{"type":"pl', 'an"}\n{"type":"st', 'ep"}\n{"type":"answer"}'), (v) =>
    seen.push(v),
  );
  assert.deepEqual(seen, [{ type: "plan" }, { type: "step" }, { type: "answer" }]);
});

test("a character split across chunks is read whole", async () => {
  const bytes = new TextEncoder().encode('{"q":"Grüße"}\n');
  const cut = bytes.indexOf(0xc3) + 1;
  const seen: unknown[] = [];
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes.slice(0, cut));
      controller.enqueue(bytes.slice(cut));
      controller.close();
    },
  });
  await readNdjson(stream, (v) => seen.push(v));
  assert.deepEqual(seen, [{ q: "Grüße" }]);
});

test("a line that is not JSON breaks the read", async () => {
  await assert.rejects(
    readNdjson(streamOf("<html>\n"), () => {}),
    SyntaxError,
  );
});
