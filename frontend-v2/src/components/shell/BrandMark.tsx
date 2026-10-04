import openProgramMark from "../../assets/openprogram-mark.svg";
import { cn } from "../../lib/utils";

/**
 * OpenProgram's own mark, shown wherever a tenant has not uploaded a logo.
 *
 * The mark is the bundled SVG asset, so changing it means replacing that one
 * file (and `public/favicon.svg`, its copy for the browser tab). Tenant uploads
 * stay raster-only; only this shipped asset is an SVG.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <img
      src={openProgramMark}
      alt="OpenProgram"
      width={40}
      height={40}
      className={cn("h-10 w-10 shrink-0 rounded-lg", className)}
    />
  );
}
