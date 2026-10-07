import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LOGO_ACCEPT,
  LOGO_MAX_BYTES,
  bytesToBase64,
  formatName,
  logoFileProblem,
  logoUploadBody,
  sniffLogoType,
} from "./logoFile.ts";

// A real 1x1 PNG.
const PNG = Uint8Array.from(
  atob(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  ),
  (char) => char.charCodeAt(0),
);
const JPEG = Uint8Array.of(0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46);
const WEBP = new TextEncoder().encode("RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00");
const SVG = new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"></svg>');
const GIF = new TextEncoder().encode("GIF89a\x01\x00\x01\x00");

test("a PNG, JPEG or WebP file within the limit can be uploaded", () => {
  for (const type of ["image/png", "image/jpeg", "image/webp"]) {
    assert.equal(logoFileProblem({ type, size: 1024 }), null, type);
  }
  assert.equal(logoFileProblem({ type: "image/png", size: LOGO_MAX_BYTES }), null);
  assert.equal(LOGO_ACCEPT, "image/png,image/jpeg,image/webp");
});

test("an SVG, another type, an empty file or one over 256 KB is refused with the reason", () => {
  assert.match(
    logoFileProblem({ type: "image/svg+xml", size: 300 }) ?? "",
    /SVG logos are not accepted because an SVG can carry script/,
  );
  for (const type of ["image/gif", "application/pdf", ""]) {
    assert.equal(logoFileProblem({ type, size: 300 }), "Choose a PNG, JPEG or WebP image.", type);
  }
  assert.equal(logoFileProblem({ type: "image/png", size: 0 }), "That file is empty.");
  assert.equal(
    logoFileProblem({ type: "image/png", size: LOGO_MAX_BYTES + 1 }),
    "A logo must be at most 256 KB; this file is 257 KB.",
  );
});

test("the format is read from the bytes, as the server reads it", () => {
  assert.equal(sniffLogoType(PNG), "image/png");
  assert.equal(sniffLogoType(JPEG), "image/jpeg");
  assert.equal(sniffLogoType(WEBP), "image/webp");
  assert.equal(sniffLogoType(SVG), null);
  assert.equal(sniffLogoType(GIF), null);
  assert.equal(sniffLogoType(new Uint8Array()), null);
  assert.equal(sniffLogoType(PNG.slice(0, 12)), null);
});

test("an upload sends plain base64, typed by what the bytes are", () => {
  assert.deepEqual(logoUploadBody(PNG), {
    body: { content_type: "image/png", data_base64: bytesToBase64(PNG) },
  });
  assert.deepEqual(logoUploadBody(SVG), { problem: "That file is not a PNG, JPEG or WebP image." });
  const large = new Uint8Array(LOGO_MAX_BYTES).map((_, index) => index % 251);
  for (const bytes of [new Uint8Array(), PNG, large]) {
    assert.equal(bytesToBase64(bytes), Buffer.from(bytes).toString("base64"));
  }
  assert.equal(formatName("image/webp"), "WebP");
});
