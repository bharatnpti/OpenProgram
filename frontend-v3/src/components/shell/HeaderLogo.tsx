import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiClient } from "../../api/client";
import { BRANDING_QUERY_KEY, sameOnEveryDay } from "../../app/queryCache";

/**
 * The 36×36 mark beside the wordmark: the tenant's logo, or OpenProgram's own
 * mark when the tenant has none.
 *
 * The slot stays empty while the branding loads, so a tenant with a logo never
 * sees the default mark flash first. A logo the browser cannot draw, or a
 * failed read, falls back to the mark rather than a broken image.
 */
export function HeaderLogo() {
  const branding = useQuery({
    queryKey: BRANDING_QUERY_KEY,
    queryFn: () => apiClient.branding(),
    // The logo rarely changes; changing it invalidates this query.
    staleTime: 5 * 60_000,
    ...sameOnEveryDay,
  });
  const [brokenSha, setBrokenSha] = useState<string | null>(null);
  const logo = branding.data?.logo ?? null;

  if (branding.isPending) {
    return <span aria-hidden className="h-9 w-9 flex-none rounded-xl bg-grey-fill" />;
  }
  if (!logo || logo.sha256 === brokenSha) {
    return <BrandMark />;
  }
  return (
    <img
      src={logo.data_url}
      alt=""
      width={36}
      height={36}
      onError={() => setBrokenSha(logo.sha256)}
      className="h-9 w-9 flex-none rounded-xl object-contain"
    />
  );
}

function BrandMark() {
  return (
    <span className="grid h-9 w-9 flex-none place-items-center rounded-xl bg-magenta" aria-hidden>
      <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="#fff" strokeWidth="2">
        <circle cx="10" cy="4" r="2" />
        <circle cx="4" cy="16" r="2" />
        <circle cx="16" cy="16" r="2" />
        <path d="M10 6v4M10 10l-5 4M10 10l5 4" strokeLinecap="round" />
      </svg>
    </span>
  );
}
