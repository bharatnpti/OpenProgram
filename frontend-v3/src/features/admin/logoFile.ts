// No imports, so `node --test` can run it.

/**
 * Checks on a chosen logo file, made before it is sent.
 *
 * The server applies the same rules and is the one that decides; checking here
 * only saves an upload that would be refused. Keep them in step with
 * backend/core/domain/branding.py and branding_service.py.
 */

export const LOGO_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export type LogoType = (typeof LOGO_TYPES)[number];

/** The `accept` list for the file input that picks a logo. */
export const LOGO_ACCEPT = LOGO_TYPES.join(",");

/** 256 KB, measured on the file itself. */
export const LOGO_MAX_BYTES = 256 * 1024;

const FORMATS = "PNG, JPEG or WebP";
const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const JPEG_SIGNATURE = [0xff, 0xd8, 0xff];
const WEBP_CHUNKS = new Set(["VP8 ", "VP8L", "VP8X"]);

export type LogoUploadBody = { content_type: LogoType; data_base64: string };

export function isLogoType(value: string): value is LogoType {
  return (LOGO_TYPES as readonly string[]).includes(value);
}

/** Why a file cannot be the logo, judged from its type and size before it is read. */
export function logoFileProblem(file: { type: string; size: number }): string | null {
  if (file.type === "image/svg+xml") {
    return `SVG logos are not accepted because an SVG can carry script. Choose a ${FORMATS} image.`;
  }
  if (!isLogoType(file.type)) return `Choose a ${FORMATS} image.`;
  if (file.size === 0) return "That file is empty.";
  if (file.size > LOGO_MAX_BYTES) {
    return `A logo must be at most 256 KB; this file is ${Math.ceil(file.size / 1024)} KB.`;
  }
  return null;
}

/**
 * The upload for a file's bytes, or why they cannot be one. The type is read
 * from the bytes' own signature, as the server reads it, so a script renamed
 * to .png is caught here and a misnamed image still uploads as what it is.
 */
export function logoUploadBody(bytes: Uint8Array): { body: LogoUploadBody } | { problem: string } {
  const type = sniffLogoType(bytes);
  if (type === null) return { problem: `That file is not a ${FORMATS} image.` };
  return { body: { content_type: type, data_base64: bytesToBase64(bytes) } };
}

/** The accepted image format the bytes are, from their leading signature. */
export function sniffLogoType(bytes: Uint8Array): LogoType | null {
  if (startsWith(bytes, PNG_SIGNATURE) && ascii(bytes, 12, 16) === "IHDR") return "image/png";
  if (startsWith(bytes, JPEG_SIGNATURE)) return "image/jpeg";
  if (
    ascii(bytes, 0, 4) === "RIFF" &&
    ascii(bytes, 8, 12) === "WEBP" &&
    WEBP_CHUNKS.has(ascii(bytes, 12, 16))
  ) {
    return "image/webp";
  }
  return null;
}

/** Standard base64, as the upload API takes it: no `data:` prefix, no line breaks. */
export function bytesToBase64(bytes: Uint8Array): string {
  // In slices, so a large file never becomes one oversized argument list.
  const slice = 0x8000;
  let binary = "";
  for (let start = 0; start < bytes.length; start += slice) {
    binary += String.fromCharCode(...bytes.subarray(start, start + slice));
  }
  return btoa(binary);
}

/** "PNG", "JPEG", "WebP". */
export function formatName(type: string): string {
  return { "image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WebP" }[type] ?? type;
}

function startsWith(bytes: Uint8Array, signature: number[]): boolean {
  return signature.every((byte, index) => bytes[index] === byte);
}

function ascii(bytes: Uint8Array, start: number, end: number): string {
  return String.fromCharCode(...bytes.subarray(start, end));
}
