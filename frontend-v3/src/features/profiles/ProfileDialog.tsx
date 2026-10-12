import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { MembershipProfileResponse, ProfileResponse } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { formatDay } from "../../lib/format";
import { refusal } from "../admin/adminErrors";
import {
  profileDone,
  profileLabel,
  profileOptions,
  profileRefusal,
  sourceWords,
  suggestionLine,
} from "./profiles";
import { useProfileCatalog } from "./useProfiles";

const overlay = "fixed inset-0 z-40 bg-black/30";
const panel =
  "fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop";
const field =
  "h-10 w-full rounded-xl border border-grey-border bg-white px-3 text-[14px] focus:border-ink";

/** Whose profile in which pod the dialog changes. */
export type ProfileTarget = {
  podId: string;
  podName: string;
  memberId: string;
  memberName: string;
};

/**
 * Change one person's profile in one pod: what it is now and where it came from, the
 * suggestion with its reasons, and a picker. The change holds from today; earlier days keep
 * the profile they had. Shown to an admin and to the scrum master of the pod, nobody else.
 */
export function ProfileDialog({
  target,
  onClose,
}: {
  target: ProfileTarget | null;
  onClose: () => void;
}) {
  return (
    <Dialog.Root
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className={overlay} />
        <Dialog.Content className={panel}>
          {target ? (
            <ProfileBody
              key={`${target.podId}:${target.memberId}`}
              target={target}
              onClose={onClose}
            />
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ProfileBody({ target, onClose }: { target: ProfileTarget; onClose: () => void }) {
  const catalog = useProfileCatalog();
  const detail = useQuery({
    queryKey: ["profiles", "membership", target.podId, target.memberId],
    queryFn: () => apiClient.membershipProfile(target.podId, target.memberId),
  });
  return (
    <>
      <Dialog.Title className="text-[20px] font-extrabold">
        {target.memberName}&rsquo;s profile in {target.podName}
      </Dialog.Title>
      <Dialog.Description className="mt-1 text-[13px] text-grey-body">
        What they bring to this pod. It grants no rights: it says who is asked and named, such as a
        release criterion&rsquo;s fix ask, the day report&rsquo;s names and Ora&rsquo;s answers.
      </Dialog.Description>
      <div className="mt-4">
        <PanelState
          isLoading={detail.isLoading || catalog.isLoading}
          error={detail.error ?? catalog.error}
          onRetry={() => void detail.refetch()}
        >
          {detail.data && catalog.data ? (
            <ProfileForm
              detail={detail.data}
              catalog={catalog.data}
              target={target}
              onClose={onClose}
            />
          ) : null}
        </PanelState>
      </div>
    </>
  );
}

function ProfileForm({
  detail,
  catalog,
  target,
  onClose,
}: {
  detail: MembershipProfileResponse;
  catalog: ProfileResponse[];
  target: ProfileTarget;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const current = detail.current;
  const [profile, setProfile] = useState(current?.profile ?? detail.suggestion.profile);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const suggested = detail.suggestion.profile;
  const unchanged = current?.checked === true && current.profile === profile;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await apiClient.setMembershipProfile(target.podId, target.memberId, {
        profile,
        accepted_suggestion: profile === suggested,
      });
      toast.success(profileDone(saved));
      // Every screen that names people or routes asks reads profiles.
      await queryClient.invalidateQueries();
      onClose();
    } catch (failure) {
      setError(profileRefusal(failure) ?? refusal(failure));
      setBusy(false);
    }
  };

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy && !unchanged) void save();
      }}
    >
      {current ? (
        <p className="text-[14px] text-grey-body">
          Now <span className="font-bold text-ink">{current.label}</span>.{" "}
          {sourceWords(current, formatDay)}
        </p>
      ) : null}
      <div className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
        <p>{suggestionLine(detail.suggestion)}</p>
        {suggested !== profile ? (
          <button
            type="button"
            className="mt-1 text-[13px] font-bold text-magenta hover:underline"
            onClick={() => setProfile(suggested)}
          >
            Use {profileLabel(catalog, suggested)}
          </button>
        ) : null}
      </div>
      <div>
        <label
          htmlFor="profile-pick"
          className="mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary"
        >
          Their profile in {target.podName}
        </label>
        <select
          id="profile-pick"
          className={field}
          value={profile}
          onChange={(event) => setProfile(event.target.value)}
        >
          {profileOptions(catalog, current?.profile).map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[12px] text-grey-secondary">
          The change holds from today; earlier days keep the profile they had.
        </p>
      </div>
      {error ? (
        <p role="alert" className="rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Dialog.Close asChild>
          <Pill variant="ghost" size="sm" disabled={busy}>
            Cancel
          </Pill>
        </Dialog.Close>
        <Pill type="submit" size="sm" disabled={busy || unchanged}>
          {busy
            ? "Saving…"
            : current && !current.checked && current.profile === profile
              ? "Confirm"
              : "Save"}
        </Pill>
      </div>
    </form>
  );
}
