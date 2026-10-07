import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ReleaseMatchKind, ReleaseResponse } from "../../api/schema";
import { ConfirmDialog } from "../../components/Dialogs";
import { Pill } from "../../components/ui/Pill";
import { formatDay } from "../../lib/format";
import { WHO, actionError } from "../reports/access";
import { FormProblem, Locked, ReportDialog, field, fieldLabel } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import { candidateLabel, releaseName } from "./overallWords";
import { useReleases } from "./queries";

const MATCH_WORDS: Record<ReleaseMatchKind, string> = {
  fix_version: "fix version",
  label: "label",
};

/** Every read and report a release's creation or removal changes. */
function useReleaseRefresh(projectId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all(
      [["releases", projectId], ["delivery", projectId], ["day-reports"]].map((queryKey) =>
        queryClient.invalidateQueries({ queryKey }),
      ),
    );
}

/**
 * Which requirements Overall covers: the whole project or one release, kept in
 * the URL (`?release=`). Releases are read with the project's progress and
 * defined, from a Jira fix version or label, by whoever sets its dates.
 */
export function ReleaseScope({
  projectId,
  releaseId,
  onChange,
}: {
  projectId: string;
  releaseId: string;
  onChange: (releaseId: string) => void;
}) {
  const access = useReportAccess();
  const { locked, query } = useReleases(projectId);
  const releases = query.data ?? [];
  const [adding, setAdding] = useState(false);

  if (locked) {
    return (
      <div className="mb-8 flex flex-wrap items-center gap-3 rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
        {releaseId ? (
          <>
            <span>Showing one release of this project, from the link.</span>
            <button type="button" className="font-bold underline" onClick={() => onChange("")}>
              Show the whole project
            </button>
          </>
        ) : (
          <span>Covers the whole project.</span>
        )}
        <Locked>Picking a release opens for {WHO.projectProgress}.</Locked>
      </div>
    );
  }

  const selected = releases.find((release) => release.release_id === releaseId);
  return (
    <div className="mb-8 flex flex-wrap items-center gap-3 rounded-2xl bg-grey-fill px-4 py-3">
      <label htmlFor="release-scope" className="text-[13px] font-bold">
        Covers
      </label>
      <select
        id="release-scope"
        className="h-10 max-w-full rounded-full border border-grey-border bg-white px-4 text-[14px] font-bold"
        value={selected ? releaseId : ""}
        disabled={query.isLoading}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">The whole project</option>
        {releases.map((release) => (
          <option key={release.release_id} value={release.release_id}>
            {releaseName(release.name)}
          </option>
        ))}
      </select>
      {selected ? (
        <span className="text-[12px] text-grey-secondary">
          Requirements with the Jira {MATCH_WORDS[selected.match_kind]} “{selected.match_value}”
        </span>
      ) : releases.length === 0 && !query.isLoading ? (
        <span className="text-[12px] text-grey-secondary">No releases defined yet.</span>
      ) : null}
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {access.setProjectDates ? (
          <>
            {selected ? (
              <RemoveRelease
                projectId={projectId}
                release={selected}
                onRemoved={() => onChange("")}
              />
            ) : null}
            <Pill size="sm" variant="ghost" onClick={() => setAdding(true)}>
              Add a release
            </Pill>
          </>
        ) : (
          <Locked>
            {access.why("setProjectDates", `Releases are defined by ${WHO.projectDates}.`)}
          </Locked>
        )}
      </div>
      {adding ? (
        <ReleaseDialog
          projectId={projectId}
          onClose={() => setAdding(false)}
          onCreated={(created) => onChange(created.release_id)}
        />
      ) : null}
    </div>
  );
}

function RemoveRelease({
  projectId,
  release,
  onRemoved,
}: {
  projectId: string;
  release: ReleaseResponse;
  onRemoved: () => void;
}) {
  const refresh = useReleaseRefresh(projectId);
  const reports = useQuery({
    queryKey: ["day-reports", "project", projectId],
    queryFn: () => apiClient.dayReports(projectId),
  });
  const covering = (reports.data ?? []).filter(
    (report) => report.release_id === release.release_id,
  );
  const remove = useMutation({
    mutationFn: () => apiClient.removeRelease(projectId, release.release_id),
    onSuccess: async () => {
      onRemoved();
      await refresh();
      toast.success(`${releaseName(release.name)} removed.`);
    },
    onError: (error) => toast.error(actionError(error)),
  });
  return (
    <ConfirmDialog
      trigger={
        <Pill size="sm" variant="ghost" disabled={remove.isPending}>
          {remove.isPending ? "Removing…" : "Remove release"}
        </Pill>
      }
      title={`Remove ${releaseName(release.name)}?`}
      description={
        `Its committed date goes with it; the requirements stay as they are in Jira.` +
        (covering.length > 0
          ? ` ${covering.map((report) => `“${report.name}”`).join(", ")} covers it and would stop being sent.`
          : "")
      }
      confirmLabel="Remove release"
      onConfirm={() => remove.mutate()}
    />
  );
}

/**
 * Define a release from what the project's Jira issues already carry: a fix
 * version or a label, offered with how many issues have it. Mounted while open.
 */
function ReleaseDialog({
  projectId,
  onClose,
  onCreated,
}: {
  projectId: string;
  onClose: () => void;
  onCreated: (release: ReleaseResponse) => void;
}) {
  const refresh = useReleaseRefresh(projectId);
  const candidates = useQuery({
    queryKey: ["release-candidates", projectId],
    queryFn: () => apiClient.releaseCandidates(projectId),
  });
  const [kind, setKind] = useState<ReleaseMatchKind>("fix_version");
  const [value, setValue] = useState("");
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () =>
      apiClient.createRelease(projectId, {
        name: name.trim() || value.trim(),
        match_kind: kind,
        match_value: value.trim(),
      }),
    onSuccess: async (created) => {
      await refresh();
      toast.success(`${releaseName(created.name)} added.`);
      onCreated(created);
      onClose();
    },
  });
  const offered = (candidates.data ?? []).filter((item) => item.kind === kind);

  return (
    <ReportDialog
      open
      onOpenChange={(next) => (next ? undefined : onClose())}
      title="Add a release"
      description="A release is the requirements that carry one Jira fix version or label. Its date and forecast then show beside the project's."
    >
      <form
        className="grid grid-cols-[minmax(0,1fr)] gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) create.mutate();
        }}
      >
        <div>
          <label htmlFor="release-kind" className={fieldLabel}>
            Defined by
          </label>
          <select
            id="release-kind"
            className={field}
            value={kind}
            onChange={(event) => {
              setKind(event.target.value as ReleaseMatchKind);
              setValue("");
            }}
          >
            <option value="fix_version">A Jira fix version</option>
            <option value="label">A Jira label</option>
          </select>
        </div>
        <div>
          <label htmlFor="release-value" className={fieldLabel}>
            {kind === "fix_version" ? "Fix version" : "Label"}
          </label>
          <select
            id="release-value"
            className={field}
            value={value}
            disabled={offered.length === 0}
            onChange={(event) => {
              setValue(event.target.value);
              if (!name.trim()) setName(event.target.value);
            }}
          >
            <option value="">
              {candidates.isLoading
                ? "Loading…"
                : offered.length === 0
                  ? `No ${kind === "fix_version" ? "fix version" : "label"} on this project's issues yet`
                  : "Pick one"}
            </option>
            {offered.map((item) => (
              <option key={item.value} value={item.value}>
                {candidateLabel(item, formatDay)}
              </option>
            ))}
          </select>
          {candidates.error ? (
            <FormProblem>{actionError(candidates.error)}</FormProblem>
          ) : !candidates.isLoading && (candidates.data ?? []).length === 0 ? (
            <p className="mt-1 text-[12px] text-grey-secondary">
              Fix versions and labels come in with the Jira sync; none of this project&apos;s
              requirements carry one yet.
            </p>
          ) : null}
        </div>
        <div>
          <label htmlFor="release-name" className={fieldLabel}>
            Name
          </label>
          <input
            id="release-name"
            className={field}
            value={name}
            maxLength={120}
            placeholder={value || "Checkout 1.0"}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        {create.error ? <FormProblem>{actionError(create.error)}</FormProblem> : null}
        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={!value.trim() || create.isPending}>
            {create.isPending ? "Adding…" : "Add release"}
          </Pill>
        </div>
      </form>
    </ReportDialog>
  );
}
