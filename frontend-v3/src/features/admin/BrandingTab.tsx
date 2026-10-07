import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageUp, Trash2 } from "lucide-react";
import { useRef, type ChangeEvent } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { TenantLogoUploadRequest } from "../../api/schema";
import { ConfirmDialog } from "../../components/Dialogs";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { TabIntro } from "./AdminBits";
import { errorText } from "./adminWords";
import { LOGO_ACCEPT, formatName, logoFileProblem, logoUploadBody } from "./logoFile";
import { savedLine, useMemberNames } from "./members";

/**
 * The key the header's logo query should share. A change invalidates it and
 * the config-scoped key, whichever the header ends up using.
 */
const BRANDING_KEY = ["branding"] as const;

/**
 * The tenant's logo, which the header shows to everyone in the tenant.
 * Uploading replaces it; removing it goes back to the OpenProgram mark. A file
 * is checked here before it is sent, and the server checks it again.
 */
export function BrandingTab() {
  const queryClient = useQueryClient();
  const nameOf = useMemberNames();
  const fileInput = useRef<HTMLInputElement>(null);
  const branding = useQuery({
    queryKey: BRANDING_KEY,
    queryFn: () => apiClient.branding(),
    staleTime: 5 * 60_000,
  });
  const logo = branding.data?.logo ?? null;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: BRANDING_KEY });
    void queryClient.invalidateQueries({ queryKey: ["config", "branding"] });
  };
  const upload = useMutation({
    mutationFn: (body: TenantLogoUploadRequest) => apiClient.uploadBrandingLogo(body),
    onSuccess: () => {
      toast.success("Logo updated. The header shows it for everyone in this tenant.");
      refresh();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const remove = useMutation({
    mutationFn: () => apiClient.removeBrandingLogo(),
    onSuccess: () => {
      toast.success("Logo removed. The header shows the OpenProgram mark again.");
      refresh();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const busy = upload.isPending || remove.isPending;

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Cleared, so picking the same file again after a refusal still counts.
    event.target.value = "";
    if (!file) return;
    const problem = logoFileProblem(file);
    if (problem) {
      toast.error(problem);
      return;
    }
    const prepared = logoUploadBody(new Uint8Array(await file.arrayBuffer()));
    if ("problem" in prepared) {
      toast.error(prepared.problem);
      return;
    }
    upload.mutate(prepared.body);
  }

  return (
    <PanelState
      needs="an admin"
      isLoading={branding.isLoading}
      error={branding.error}
      onRetry={() => void branding.refetch()}
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <TabIntro>
          The logo in the header, shown to everyone in this tenant. PNG, JPEG or WebP, up to 256 KB.
          Without one, the header shows the OpenProgram mark.
        </TabIntro>
        <section className="grid gap-5 rounded-3xl border border-grey-border bg-white p-5">
          <div className="flex flex-wrap items-center gap-6">
            <div
              role="group"
              aria-label="How the header looks"
              className="flex items-center gap-3 rounded-2xl border border-grey-border bg-white px-4 py-3"
            >
              {logo ? (
                <img
                  src={logo.data_url}
                  alt="Tenant logo"
                  className="h-9 max-w-[160px] rounded-xl object-contain"
                />
              ) : (
                <DefaultMark />
              )}
              <span className="leading-tight">
                <span className="block text-[16px] font-extrabold">OpenProgram</span>
                <span className="block text-[11px] text-grey-secondary">Delivery intelligence</span>
              </span>
            </div>
            <p className="min-w-0 flex-1 text-[13px] text-grey-body">
              {logo
                ? [
                    `${formatName(logo.content_type)} logo.`,
                    savedLine(logo.updated_at, logo.updated_by, nameOf),
                  ]
                    .filter(Boolean)
                    .join(" ")
                : "No logo uploaded. The header shows the OpenProgram mark."}
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <input
              ref={fileInput}
              type="file"
              accept={LOGO_ACCEPT}
              className="sr-only"
              tabIndex={-1}
              aria-hidden
              onChange={(event) => void chooseFile(event)}
            />
            <Pill size="sm" disabled={busy} onClick={() => fileInput.current?.click()}>
              <ImageUp size={14} aria-hidden />
              {upload.isPending ? "Uploading…" : logo ? "Upload a new logo" : "Upload a logo"}
            </Pill>
            {logo ? (
              <ConfirmDialog
                trigger={
                  <Pill variant="ghost" size="sm" disabled={busy}>
                    <Trash2 size={14} aria-hidden />
                    {remove.isPending ? "Removing…" : "Remove logo"}
                  </Pill>
                }
                title="Remove the logo?"
                description="The header goes back to the OpenProgram mark for everyone in this tenant. You can upload a logo again at any time."
                confirmLabel="Remove logo"
                onConfirm={() => remove.mutate()}
              />
            ) : null}
          </div>
        </section>
      </div>
    </PanelState>
  );
}

/** The OpenProgram mark, as the header draws it. */
function DefaultMark() {
  return (
    <span className="grid h-9 w-9 place-items-center rounded-xl bg-magenta" aria-hidden>
      <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="#fff" strokeWidth="2">
        <circle cx="10" cy="4" r="2" />
        <circle cx="4" cy="16" r="2" />
        <circle cx="16" cy="16" r="2" />
        <path d="M10 6v4M10 10l-5 4M10 10l5 4" strokeLinecap="round" />
      </svg>
    </span>
  );
}
