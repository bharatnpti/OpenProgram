import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ImageUp, Trash2 } from "lucide-react";
import { useRef, useState, type ChangeEvent } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { TenantLogoResponse, TenantLogoUploadRequest } from "../../api/schema";
import { HeaderLogo } from "../../components/shell/HeaderLogo";
import { Card } from "../../components/ui/Card";
import { Pill } from "../../components/ui/Pill";
import { ConfirmDialog } from "../admin/ConfirmDialog";
import { errorMessage, type ConfirmState } from "../admin/adminTypes";
import { LOGO_ACCEPT, logoFileProblem, logoUploadBody } from "./logoFile";
import { BRANDING_QUERY_KEY, useBranding } from "./useBranding";

const FORMAT_LABELS: Record<TenantLogoResponse["content_type"], string> = {
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "image/webp": "WebP",
};

/**
 * The tenant's logo, which the console header shows to everyone in the tenant.
 *
 * Uploading replaces it and removing it goes back to OpenProgram's mark; either
 * way the header follows at once. A file is checked here before it is sent,
 * and the server checks it again.
 */
export function BrandingCard() {
  const queryClient = useQueryClient();
  const branding = useBranding();
  const fileInput = useRef<HTMLInputElement>(null);
  const [confirm, setConfirm] = useState<ConfirmState>({ open: false });
  const logo = branding.data?.logo ?? null;

  const upload = useMutation({
    mutationFn: (body: TenantLogoUploadRequest) => apiClient.uploadBrandingLogo(body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: BRANDING_QUERY_KEY });
      toast.success("Logo updated. The header shows it for everyone in this tenant.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const remove = useMutation({
    mutationFn: apiClient.removeBrandingLogo,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: BRANDING_QUERY_KEY });
      toast.success("Logo removed. The header shows the OpenProgram mark again.");
    },
    onError: (error) => toast.error(errorMessage(error)),
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
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-[18px] font-bold">Branding</h2>
        <p className="mt-1 max-w-[640px] text-[13px] text-grey-secondary">
          The logo in the console header, shown to everyone in this tenant. PNG, JPEG or WebP, up to
          256 KB. Without one, the header shows the OpenProgram mark.
        </p>
      </div>

      <Card padding="p-5" className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center gap-6">
          <div
            role="group"
            aria-label="Header preview"
            className="flex items-center gap-3 rounded-2xl border border-grey-border bg-white px-4 py-3"
          >
            <HeaderLogo logo={logo} loading={branding.isPending} />
            <div>
              <div className="text-[18px] font-extrabold tracking-tight">OpenProgram</div>
              <div className="text-xs text-grey-secondary">Delivery intelligence</div>
            </div>
          </div>
          <p className="min-w-0 flex-1 text-[13px] text-grey-secondary">
            {branding.isPending
              ? "Loading the current logo…"
              : logo
                ? `${FORMAT_LABELS[logo.content_type]} logo, uploaded ${new Date(
                    logo.updated_at,
                  ).toLocaleString()} by ${logo.updated_by}.`
                : "No logo uploaded. The header shows the OpenProgram mark."}
          </p>
        </div>

        {branding.isError ? (
          <p className="text-[14px] text-rag-red">{errorMessage(branding.error)}</p>
        ) : null}

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
            <ImageUp size={14} />
            {upload.isPending ? "Uploading…" : "Upload logo"}
          </Pill>
          <Pill
            variant="ghost"
            size="sm"
            disabled={busy || !logo}
            onClick={() =>
              setConfirm({
                open: true,
                title: "Remove the logo?",
                description:
                  "The header goes back to the OpenProgram mark for everyone in this tenant. You can upload a logo again at any time.",
                confirmLabel: "Remove logo",
                destructive: true,
                onConfirm: () => remove.mutate(),
              })
            }
          >
            <Trash2 size={14} />
            {remove.isPending ? "Removing…" : "Remove logo"}
          </Pill>
        </div>
      </Card>

      <ConfirmDialog
        state={confirm}
        onOpenChange={(open) => !open && setConfirm({ open: false })}
      />
    </div>
  );
}
