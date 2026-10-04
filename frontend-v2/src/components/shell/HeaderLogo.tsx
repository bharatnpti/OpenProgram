import { useState } from "react";

import type { TenantLogoResponse } from "../../api/schema";
import { BrandMark } from "./BrandMark";

/**
 * The 40×40 slot beside the wordmark: the tenant's logo, or OpenProgram's mark
 * when the tenant has none.
 *
 * The slot stays empty while the branding loads, so a tenant with a logo never
 * sees the default mark flash first. A logo the browser cannot draw falls back
 * to the mark rather than a broken image.
 */
export function HeaderLogo({
  logo,
  loading = false,
}: {
  logo: TenantLogoResponse | null;
  loading?: boolean;
}) {
  const [brokenSha, setBrokenSha] = useState<string | null>(null);
  if (loading) {
    return <div aria-hidden className="h-10 w-10 shrink-0 rounded-lg bg-grey-fill" />;
  }
  if (!logo || logo.sha256 === brokenSha) {
    return <BrandMark />;
  }
  return (
    <img
      src={logo.data_url}
      alt="Logo"
      width={40}
      height={40}
      onError={() => setBrokenSha(logo.sha256)}
      className="h-10 w-10 shrink-0 rounded-lg object-contain"
    />
  );
}
