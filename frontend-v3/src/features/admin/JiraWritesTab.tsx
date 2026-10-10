import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  JiraCreateProjectsDto,
  JiraCreateProjectsUpdate,
  JiraWriteKindDto,
  JiraWriteSwitchDto,
  JiraWritesChangeDto,
  JiraWritesUpdateRequest,
} from "../../api/schema";
import { useReadOnly } from "../../app/viewingDate";
import { ConfirmDialog } from "../../components/Dialogs";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay, formatTime } from "../../lib/format";
import { Problems, TabIntro, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText } from "./adminWords";
import {
  CONFIRM_MASTER_ON,
  JIRA_WRITES_KEY,
  KIND_ORDER,
  KIND_WORDS,
  MASTER_LABEL,
  PROJECTS_LABEL,
  changeLine,
  kindBody,
  kindOf,
  kindStateLine,
  masterLine,
  projectProblems,
  projectsBody,
  projectsWords,
  sourceWords,
} from "./jiraWritesWords";

/**
 * Every way OpenProgram writes to Jira, switched in one place: the tenant's
 * switch first (off, nothing is written at all), then one switch per kind of
 * write, each saying what it allows and what still guards it, and the projects
 * release readiness may create issues in. Admins only: the Admin page is
 * theirs. Every change is kept, newest first.
 */
export function JiraWritesTab() {
  const queryClient = useQueryClient();
  const writes = useQuery({ queryKey: JIRA_WRITES_KEY, queryFn: () => apiClient.jiraWrites() });
  const save = useMutation({
    mutationFn: (body: JiraWritesUpdateRequest) => apiClient.saveJiraWrites(body),
    onSuccess: (saved) => {
      queryClient.setQueryData(JIRA_WRITES_KEY, saved);
      toast.success("Saved. It applies to the next write.");
      // Check-ins, Release readiness and its board, and Today's task tick read these.
      void queryClient.invalidateQueries({ queryKey: ["config", "tenant-writeback"] });
      void queryClient.invalidateQueries({ queryKey: ["config", "readiness"] });
      void queryClient.invalidateQueries({ queryKey: ["readiness"] });
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError: (error) => {
      toast.error(errorText(error));
      void queryClient.invalidateQueries({ queryKey: JIRA_WRITES_KEY });
    },
  });
  const data = writes.data;

  return (
    <PanelState
      isLoading={writes.isLoading}
      error={writes.error}
      onRetry={() => void writes.refetch()}
    >
      {data ? (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
          <TabIntro>
            Every way OpenProgram writes to this tenant&apos;s Jira. The tenant&apos;s switch comes
            first: while it is off, nothing is written to Jira at all. Each kind of write below has
            its own switch, read only while the tenant&apos;s switch is on, and keeps its own
            guards.
          </TabIntro>
          <MasterCard
            master={data.master}
            saving={save.isPending}
            onSet={(on) => save.mutate({ master: on })}
          />
          <section className="grid gap-3" aria-labelledby="jw-kinds">
            <h3 id="jw-kinds" className="text-[17px] font-extrabold">
              Kinds of write
            </h3>
            <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-3">
              {KIND_ORDER.map((kind) => (
                <KindCard
                  key={kind}
                  kind={kindOf(data, kind)}
                  master={data.master}
                  saving={save.isPending}
                  onSet={(on) => save.mutate(kindBody(kind, on))}
                />
              ))}
            </div>
          </section>
          <ProjectsCard
            current={data.create_projects}
            saving={save.isPending}
            onSave={(body) => save.mutate({ create_projects: body })}
          />
          <ChangesCard changes={data.changes} />
        </div>
      ) : null}
    </PanelState>
  );
}

function MasterCard({
  master,
  saving,
  onSet,
}: {
  master: JiraWriteSwitchDto;
  saving: boolean;
  onSet: (on: boolean) => void;
}) {
  const { readOnly, reason } = useReadOnly();
  return (
    <section
      aria-labelledby="jw-master"
      className="grid gap-3 rounded-3xl border border-grey-border bg-white p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id="jw-master" className="text-[17px] font-extrabold">
              {MASTER_LABEL}
            </h3>
            <RagChip tone={master.on ? "success" : "neutral"} className="h-6 px-2.5 text-[12px]">
              {master.on ? "On" : "Off"}
            </RagChip>
          </div>
          <p className="mt-1 text-[14px] text-grey-body">{masterLine(master)}</p>
          <p className={hintClass}>{sourceWords(master.source)}</p>
        </div>
        {master.on ? (
          <Pill
            variant="ghost"
            size="sm"
            disabled={saving || readOnly}
            title={readOnly ? (reason ?? undefined) : undefined}
            onClick={() => onSet(false)}
          >
            Turn off
          </Pill>
        ) : (
          <ConfirmDialog
            trigger={
              <Pill size="sm" disabled={saving}>
                Turn on…
              </Pill>
            }
            title={CONFIRM_MASTER_ON.title}
            description={CONFIRM_MASTER_ON.description}
            confirmLabel={CONFIRM_MASTER_ON.confirmLabel}
            onConfirm={() => onSet(true)}
          />
        )}
      </div>
    </section>
  );
}

function KindCard({
  kind,
  master,
  saving,
  onSet,
}: {
  kind: JiraWriteKindDto;
  master: JiraWriteSwitchDto;
  saving: boolean;
  onSet: (on: boolean) => void;
}) {
  const { readOnly, reason } = useReadOnly();
  const words = KIND_WORDS[kind.kind];
  const id = `jw-${kind.kind}`;
  return (
    <section className="grid min-w-0 content-start gap-3 rounded-3xl border border-grey-border bg-white p-5">
      <div className="flex items-start gap-3">
        <input
          id={id}
          type="checkbox"
          role="switch"
          aria-checked={kind.on}
          checked={kind.on}
          disabled={saving || readOnly}
          title={readOnly ? (reason ?? undefined) : undefined}
          className="mt-1 h-4 w-4 flex-none"
          onChange={(event) => onSet(event.target.checked)}
        />
        <label htmlFor={id} className="min-w-0">
          <span className="block text-[15px] font-extrabold">{words.label}</span>
          <span className="mt-1 flex flex-wrap items-center gap-2">
            <RagChip
              tone={kind.effective ? "success" : kind.on ? "warning" : "neutral"}
              className="h-6 px-2.5 text-[12px]"
            >
              {kind.effective ? "Writes now" : kind.on ? "On, held" : "Off"}
            </RagChip>
            <span className="text-[12px] text-grey-secondary">
              {kind.source === "default" ? words.byDefault : sourceWords(kind.source)}
            </span>
          </span>
        </label>
      </div>
      <p className="text-[13px] text-grey-body">{kindStateLine(kind, master)}</p>
      <div>
        <p className={labelClass}>What it allows</p>
        <p className="text-[13px] text-grey-body">{words.allows}</p>
      </div>
      <div>
        <p className={labelClass}>Still guarded by</p>
        <ul className="grid list-disc gap-1 pl-5 text-[13px] text-grey-body">
          {words.guards.map((guard) => (
            <li key={guard}>{guard}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function ProjectsCard({
  current,
  saving,
  onSave,
}: {
  current: JiraCreateProjectsDto;
  saving: boolean;
  onSave: (body: JiraCreateProjectsUpdate) => void;
}) {
  const { readOnly, reason } = useReadOnly();
  const [own, setOwn] = useState(current.own_project);
  const [text, setText] = useState(current.projects.join(", "));
  const [attempted, setAttempted] = useState(false);
  const problems = projectProblems(own, text);
  const body = projectsBody(current, own, text);
  return (
    <section
      aria-labelledby="jw-projects"
      className="grid gap-3 rounded-3xl border border-grey-border bg-white p-5"
    >
      <div>
        <h3 id="jw-projects" className="text-[17px] font-extrabold">
          {PROJECTS_LABEL}
        </h3>
        <p className="mt-1 text-[13px] text-grey-body">
          Where Create release-readiness issues may put a new issue: now {projectsWords(current)}.
          Creating into any other Jira project is refused. {sourceWords(current.source)}
        </p>
      </div>
      <label className="flex items-start gap-3 text-[14px]">
        <input
          type="checkbox"
          checked={own}
          disabled={readOnly}
          className="mt-1 h-4 w-4 flex-none"
          onChange={(event) => setOwn(event.target.checked)}
        />
        <span>
          <span className="block font-bold">The scope&apos;s own Jira project</span>
          <span className="block text-[12.5px] text-grey-body">
            A project&apos;s or release&apos;s own project key; for a pod, the projects it works on.
          </span>
        </span>
      </label>
      <div>
        <label htmlFor="jw-project-keys" className={labelClass}>
          Other Jira projects
        </label>
        <input
          id="jw-project-keys"
          className={inputClass}
          value={text}
          disabled={readOnly}
          placeholder="SEC, OPS"
          onChange={(event) => setText(event.target.value)}
        />
        <p className={hintClass}>Project keys, separated by commas. Leave empty for none.</p>
      </div>
      {attempted ? <Problems problems={problems} /> : null}
      <div className="flex justify-end">
        <Pill
          size="sm"
          disabled={saving || readOnly || body === null}
          title={readOnly ? (reason ?? undefined) : undefined}
          onClick={() => {
            setAttempted(true);
            if (problems.length === 0 && body !== null) onSave(body);
          }}
        >
          {saving ? "Saving…" : "Save the projects"}
        </Pill>
      </div>
    </section>
  );
}

function ChangesCard({ changes }: { changes: JiraWritesChangeDto[] }) {
  return (
    <section aria-labelledby="jw-changes" className="grid gap-2">
      <h3 id="jw-changes" className="text-[17px] font-extrabold">
        Changes
      </h3>
      {changes.length === 0 ? (
        <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
          Nobody has changed these yet: every switch is at its default.
        </p>
      ) : (
        <ul className="grid gap-3 text-[13px] sm:gap-1.5">
          {changes.map((change) => (
            <li
              key={`${change.at}-${change.setting}`}
              className="grid gap-0.5 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-3"
            >
              <span className="text-grey-secondary">
                {formatDay(change.at)}, {formatTime(change.at)}
              </span>
              <span className="text-grey-body">{changeLine(change)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
