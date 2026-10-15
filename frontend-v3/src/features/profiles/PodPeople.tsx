import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { useReadOnly } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { Panel, Row } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { formatDay } from "../../lib/format";
import { ConfirmChange, type Pending } from "../admin/AdminDialog";
import { podRoleLabel } from "../admin/structure";
import { ProfileDialog, type ProfileTarget } from "./ProfileDialog";
import {
  acceptAllWords,
  isChecked,
  profileRefusal,
  reviewOrder,
  reviewSummary,
  sourceWords,
  suggestionLine,
} from "./profiles";
import { usePodProfiles, useProfileReview } from "./useProfiles";

/**
 * A pod's people: each one's role in the pod and the profile they bring to it. Whoever reads
 * the pod's panel reads this; only the people who set profiles here (an admin, the scrum
 * master who runs the pod: the server's `can_edit`) get Change and the review of the profiles
 * nobody checked yet. `schedule` adds each person's check-in time and time zone, where the
 * reader is sent them (features/profiles/schedule.ts).
 */
export function PodPeoplePanel({
  podId,
  schedule = null,
}: {
  podId: string;
  schedule?: ReadonlyMap<string, string> | null;
}) {
  const people = usePodProfiles(podId);
  const canEdit = people.data?.can_edit === true;
  const review = useProfileReview(podId, canEdit);
  const { readOnly, reason } = useReadOnly();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<ProfileTarget | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const items = reviewOrder(review.data?.items ?? []);
  const suggested = new Map(items.map((item) => [item.current.member_id, item.suggestion]));
  const summary = reviewSummary(items.length);

  const acceptAll = () => {
    const words = acceptAllWords(items);
    setPending({
      key: `accept:${podId}:${Date.now()}`,
      title: words.title,
      effect: words.effect,
      names: words.names,
      confirmLabel: "Accept all",
      destructive: false,
      run: async () => {
        try {
          const done = await apiClient.acceptProfiles({
            items: items.map((item) => ({
              pod_id: item.current.pod_id,
              member_id: item.current.member_id,
              profile: item.suggestion.profile,
            })),
          });
          toast.success(
            `Accepted ${done.accepted.length} ${done.accepted.length === 1 ? "suggestion" : "suggestions"}.`,
          );
          await queryClient.invalidateQueries();
        } catch (failure) {
          const words403 = profileRefusal(failure);
          throw words403 ? new Error(words403) : failure;
        }
      },
    });
  };

  const pod = people.data;
  return (
    <Panel title="People and profiles" note={pod ? `${pod.members.length} in the pod` : undefined}>
      <PanelState
        isLoading={people.isLoading}
        error={people.error}
        onRetry={() => void people.refetch()}
        isEmpty={(pod?.members ?? []).length === 0}
        emptyText="Nobody is in this pod yet."
      >
        {canEdit && summary ? (
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
            <span>{summary} Each has a suggestion below.</span>
            <Pill
              size="sm"
              variant="dark"
              disabled={readOnly}
              title={readOnly ? (reason ?? undefined) : undefined}
              onClick={acceptAll}
            >
              Accept all suggestions
            </Pill>
          </div>
        ) : null}
        <ul>
          {(pod?.members ?? []).map((member) => {
            const suggestion = suggested.get(member.member_id);
            return (
              <Row
                key={member.member_id}
                title={member.member_name}
                meta={[podRoleLabel(member.role), member.label].filter(Boolean).join(" · ")}
                right={
                  canEdit ? (
                    <Pill
                      size="sm"
                      variant="ghost"
                      className="h-8 px-3 text-[12px]"
                      disabled={readOnly}
                      title={readOnly ? (reason ?? undefined) : undefined}
                      aria-label={`Change ${member.member_name}'s profile in ${member.pod_name}`}
                      onClick={() =>
                        setTarget({
                          podId: member.pod_id,
                          podName: member.pod_name,
                          memberId: member.member_id,
                          memberName: member.member_name,
                        })
                      }
                    >
                      Change
                    </Pill>
                  ) : undefined
                }
              >
                <p className="mt-0.5 text-[12px] text-grey-secondary">
                  {isChecked(member) ? sourceWords(member, formatDay) : "From their pod role."}
                </p>
                {schedule?.get(member.member_id) ? (
                  <p className="mt-0.5 text-[12px] text-grey-body">
                    {schedule.get(member.member_id)}
                  </p>
                ) : null}
                {canEdit && suggestion && suggestion.profile !== member.profile ? (
                  <p className="mt-0.5 text-[12px] text-grey-body">{suggestionLine(suggestion)}</p>
                ) : null}
              </Row>
            );
          })}
        </ul>
      </PanelState>
      <ProfileDialog target={target} onClose={() => setTarget(null)} />
      <ConfirmChange pending={pending} onClose={() => setPending(null)} />
    </Panel>
  );
}
