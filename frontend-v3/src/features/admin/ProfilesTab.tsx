import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ProfileResponse } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { useProfileCatalog } from "../profiles/useProfiles";
import { Problems, TabIntro, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText } from "./adminWords";
import {
  draftFromProfile,
  newProfileDraft,
  profileBody,
  profileChanged,
  profileProblems,
  profileSaved,
  slugFromLabel,
  type ProfileDraft,
} from "./profileForm";

/**
 * The profiles people can bring to a pod: rename one to the tenant's own word, hide one it
 * does not use, or add one. A profile grants no rights; it says who is asked and named. Admins
 * only (the Admin page is theirs).
 */
export function ProfilesTab() {
  const catalog = useProfileCatalog();
  const entries = catalog.data ?? [];
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <TabIntro>
        What a person can bring to a pod. Rename a profile to your own word, hide one you
        don&rsquo;t use, or add one. A hidden profile is offered nowhere; people who hold it keep
        it. A profile grants no rights: it says who is asked and named.
      </TabIntro>
      <PanelState
        isLoading={catalog.isLoading}
        error={catalog.error}
        onRetry={() => void catalog.refetch()}
      >
        <Panel title="Profiles" note={`${entries.length} in the list`}>
          <ul className="grid">
            {entries.map((entry) => (
              <ProfileRow key={entry.slug} entry={entry} catalog={entries} />
            ))}
          </ul>
        </Panel>
        <AddProfile catalog={entries} />
      </PanelState>
    </div>
  );
}

function useSave(onDone: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, draft }: { slug: string; draft: ProfileDraft; isNew: boolean }) =>
      apiClient.saveProfile(slug, profileBody(draft)),
    onSuccess: async (saved, { isNew }) => {
      toast.success(profileSaved(saved.label, isNew, !saved.hidden));
      // Every picker and every pod's people read the labels.
      await queryClient.invalidateQueries();
      onDone();
    },
    onError: (error) => toast.error(errorText(error)),
  });
}

function Fields({
  id,
  draft,
  onChange,
}: {
  id: string;
  draft: ProfileDraft;
  onChange: (next: ProfileDraft) => void;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
      <div>
        <label htmlFor={`${id}-label`} className={labelClass}>
          Name
        </label>
        <input
          id={`${id}-label`}
          className={inputClass}
          value={draft.label}
          maxLength={40}
          onChange={(event) => onChange({ ...draft, label: event.target.value })}
        />
      </div>
      <div>
        <label htmlFor={`${id}-words`} className={labelClass}>
          Title words that suggest it
        </label>
        <input
          id={`${id}-words`}
          className={inputClass}
          value={draft.titleWordsText}
          placeholder="qa, test, quality"
          onChange={(event) => onChange({ ...draft, titleWordsText: event.target.value })}
        />
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2 md:col-span-2">
        <label className="flex items-center gap-1.5 text-[14px]">
          <input
            type="checkbox"
            checked={draft.codeWork}
            onChange={(event) => onChange({ ...draft, codeWork: event.target.checked })}
          />
          Their review is code work (a merge request is expected)
        </label>
        <label className="flex items-center gap-1.5 text-[14px]">
          <input
            type="checkbox"
            checked={draft.offered}
            onChange={(event) => onChange({ ...draft, offered: event.target.checked })}
          />
          Offered in the pickers
        </label>
      </div>
    </div>
  );
}

function ProfileRow({ entry, catalog }: { entry: ProfileResponse; catalog: ProfileResponse[] }) {
  const [draft, setDraft] = useState(() => draftFromProfile(entry));
  const save = useSave(() => undefined);
  const changed = profileChanged(entry, draft);
  const problems = changed ? profileProblems(draft, entry.slug, catalog, false) : [];
  return (
    <li className="grid gap-2 border-t border-grey-border py-4 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[15px] font-extrabold">{entry.label}</span>
        <span className="font-mono text-[11px] text-grey-secondary">{entry.slug}</span>
        {entry.hidden ? (
          <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
            hidden
          </RagChip>
        ) : null}
        {entry.customized ? (
          <RagChip tone="info" className="h-6 px-2.5 text-[12px]">
            changed here
          </RagChip>
        ) : null}
      </div>
      <form
        className="grid gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (changed && problems.length === 0 && !save.isPending) {
            save.mutate({ slug: entry.slug, draft, isNew: false });
          }
        }}
      >
        <Fields id={`profile-${entry.slug}`} draft={draft} onChange={setDraft} />
        <Problems problems={problems} />
        {changed ? (
          <div className="flex gap-2">
            <Pill
              type="submit"
              size="sm"
              variant="dark"
              disabled={problems.length > 0 || save.isPending}
            >
              {save.isPending ? "Saving…" : `Save ${entry.label}`}
            </Pill>
            <Pill
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setDraft(draftFromProfile(entry))}
            >
              Undo
            </Pill>
          </div>
        ) : null}
      </form>
    </li>
  );
}

function AddProfile({ catalog }: { catalog: ProfileResponse[] }) {
  const [draft, setDraft] = useState<ProfileDraft>(() => newProfileDraft(catalog));
  const [attempted, setAttempted] = useState(false);
  const slug = slugFromLabel(draft.label);
  const problems = profileProblems(draft, slug, catalog, true);
  const save = useSave(() => {
    setDraft(newProfileDraft(catalog));
    setAttempted(false);
  });
  return (
    <Panel title="Add a profile">
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setAttempted(true);
          if (problems.length === 0 && !save.isPending) {
            save.mutate({
              slug,
              draft: { ...draft, sort: newProfileDraft(catalog).sort },
              isNew: true,
            });
          }
        }}
      >
        <Fields id="profile-new" draft={draft} onChange={setDraft} />
        <p className={hintClass}>
          {slug ? `Kept under the id ${slug}.` : "The id comes from its name."} It is offered
          wherever a profile is picked, and suggested to people whose title has one of its words.
        </p>
        {attempted ? <Problems problems={problems} /> : null}
        <div>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Adding…" : "Add the profile"}
          </Pill>
        </div>
      </form>
    </Panel>
  );
}
