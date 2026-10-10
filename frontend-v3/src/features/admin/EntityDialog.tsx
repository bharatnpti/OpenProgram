import { useMutation } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse, ConfigNodeUpdateRequest } from "../../api/schema";
import { DayInput } from "../../components/ui/DayInput";
import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { refusal } from "./adminErrors";
import { FormDialog, fieldError, fieldHelp, fieldInput, fieldLabel } from "./AdminDialog";
import {
  WORKSTREAM_PHASES,
  WORKSTREAM_TYPES,
  buildUpdate,
  formFromNode,
  hasChanges,
  podRepoProblem,
  projectRepoProblem,
  repoList,
  validate,
  type EntityForm,
} from "./entityForm";
import {
  ENTITY_WORDS,
  newlyOutsideScope,
  podRepoScope,
  parentsOf,
  reposOf,
  type EditableKind,
  type RepoScope,
} from "./structure";
import { useStructureChanged } from "./useStructure";

const UPDATERS: Record<
  EditableKind,
  (id: string, body: ConfigNodeUpdateRequest) => Promise<ConfigNodeResponse>
> = {
  program: (id, body) => apiClient.updateConfigProgram(id, body),
  project: (id, body) => apiClient.updateConfigProject(id, body),
  pod: (id, body) => apiClient.updateConfigPod(id, body),
  workstream: (id, body) => apiClient.updateConfigWorkstream(id, body),
};

const textarea =
  "w-full rounded-xl border border-grey-border bg-white px-3 py-2 text-[14px] focus:border-ink";

function Field({
  id,
  label,
  help,
  error,
  children,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className={fieldLabel}>
        {label}
      </label>
      {children}
      {error ? (
        <p role="alert" className={fieldError}>
          {error}
        </p>
      ) : help ? (
        <p className={fieldHelp}>{help}</p>
      ) : null}
    </div>
  );
}

/** The options of a list, plus the value already stored when the list does not offer it. */
function withCurrent(base: { value: string; label: string }[], current: string, label = current) {
  if (!current || base.some((option) => option.value === current)) return base;
  return [...base, { value: current, label }];
}

/**
 * Change a program, project, pod or workstream. The id is the graph's own and never changes.
 * Only the fields that differ are sent, and the two Git rules (a pod reads only what its
 * projects list; a project keeps what a pod still reads) are checked before the save, because
 * the backend accepts a breach and the Git sync then refuses to start.
 */
export function EntityDialog({
  kind,
  node,
  members,
  scope,
  nameOf,
  onClose,
}: {
  kind: EditableKind;
  node: ConfigNodeResponse;
  members: ConfigNodeResponse[];
  scope: RepoScope;
  nameOf: (id: string) => string;
  onClose: () => void;
}) {
  const changed = useStructureChanged();
  const [form, setForm] = useState<EntityForm>(() => formFromNode(node));
  const [failure, setFailure] = useState<string | null>(null);
  const set = (patch: Partial<EntityForm>) => setForm((current) => ({ ...current, ...patch }));

  // The Git rules, read live from what is typed.
  const typed = repoList(form.repos);
  const stored = reposOf(node);
  let repoProblem: string | null = null;
  let allowedRepos: string[] = [];
  if (kind === "pod") {
    allowedRepos = podRepoScope(node.id, scope);
    const projectNames = parentsOf(scope.links.projectPods, node.id).map(nameOf);
    repoProblem = podRepoProblem(
      typed.filter((repo) => !stored.includes(repo)),
      allowedRepos,
      projectNames,
    );
  }
  if (kind === "project") {
    const after: RepoScope = {
      ...scope,
      projectRepos: { ...scope.projectRepos, [node.id]: typed },
    };
    repoProblem = projectRepoProblem(
      newlyOutsideScope(scope, after).map((item) => ({
        podName: nameOf(item.podId),
        repos: item.repos,
      })),
    );
  }

  const errors = validate(form, { repoProblem });
  const update = buildUpdate(kind, node, form);
  const ready = hasChanges(update) && Object.keys(errors).length === 0;

  const save = useMutation({
    mutationFn: () => UPDATERS[kind](node.id, update),
    onSuccess: async (saved) => {
      toast.success(`Saved ${saved.name}.`);
      await changed();
      onClose();
    },
    onError: (error) => setFailure(refusal(error)),
  });

  const word = ENTITY_WORDS[kind].one;
  const people = [...members]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((member) => ({ value: member.id, label: member.name }));
  const person = (current: string) => withCurrent(people, current, `${current} (not a member)`);

  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`Change ${node.name}`}
      description={
        <>
          Its id <span className="font-mono text-[12px]">{node.id}</span> stays as it is.
          {kind === "project" || kind === "pod"
            ? " Jira and Git changes apply from the next sync."
            : ""}
        </>
      }
    >
      <form
        className="mt-4 grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setFailure(null);
          if (ready) save.mutate();
        }}
      >
        <Field id="entity-name" label="Name" error={errors.name}>
          <input
            id="entity-name"
            className={fieldInput}
            value={form.name}
            onChange={(event) => set({ name: event.target.value })}
          />
        </Field>
        <Field id="entity-description" label="Description (optional)">
          <textarea
            id="entity-description"
            rows={2}
            className={textarea}
            value={form.description}
            onChange={(event) => set({ description: event.target.value })}
          />
        </Field>

        {kind === "project" || node.code ? (
          <Field id="entity-code" label="Short code (optional)" help="For example CHK.">
            <input
              id="entity-code"
              className={fieldInput}
              value={form.code}
              onChange={(event) => set({ code: event.target.value })}
            />
          </Field>
        ) : null}

        {kind === "project" ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                id="entity-jira-key"
                label="Jira project key"
                help="The Jira sync reads this project's issues."
              >
                <input
                  id="entity-jira-key"
                  className={fieldInput}
                  value={form.jiraProjectKey}
                  onChange={(event) => set({ jiraProjectKey: event.target.value })}
                />
              </Field>
              <Field
                id="entity-jira-board"
                label="Jira board id"
                help="Optional. The number in the board's address."
              >
                <input
                  id="entity-jira-board"
                  className={fieldInput}
                  value={form.jiraBoardId}
                  onChange={(event) => set({ jiraBoardId: event.target.value })}
                />
              </Field>
            </div>
            <details
              open={Boolean(node.jira_base_jql)}
              className="rounded-2xl bg-grey-fill px-4 py-3"
            >
              <summary className="cursor-pointer text-[13px] font-bold">
                Advanced: choose the issues with a Jira search
              </summary>
              <div className="mt-3">
                <Field
                  id="entity-jira-jql"
                  label="Jira search (JQL)"
                  help="Replaces the default search for the project key. Leave empty to read the whole Jira project."
                >
                  <textarea
                    id="entity-jira-jql"
                    rows={3}
                    className={cn(textarea, "font-mono text-[13px]")}
                    value={form.jiraBaseJql}
                    onChange={(event) => set({ jiraBaseJql: event.target.value })}
                  />
                </Field>
              </div>
            </details>
          </>
        ) : null}

        {kind === "pod" ? (
          <details
            open={Boolean(node.jira_filter_jql)}
            className="rounded-2xl bg-grey-fill px-4 py-3"
          >
            <summary className="cursor-pointer text-[13px] font-bold">
              Advanced: narrow the project's issues to this pod
            </summary>
            <div className="mt-3">
              <Field
                id="entity-jira-filter"
                label="Jira filter (JQL)"
                help="Added to the project's search. Leave empty and the pod covers the whole project."
              >
                <textarea
                  id="entity-jira-filter"
                  rows={3}
                  className={cn(textarea, "font-mono text-[13px]")}
                  value={form.jiraFilterJql}
                  onChange={(event) => set({ jiraFilterJql: event.target.value })}
                />
              </Field>
            </div>
          </details>
        ) : null}

        {kind === "workstream" ? (
          <fieldset className="grid gap-4 rounded-2xl bg-grey-fill p-4">
            <legend className="px-1 text-[13px] font-bold">Details (all optional)</legend>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="entity-type" label="Type">
                <select
                  id="entity-type"
                  className={fieldInput}
                  value={form.type}
                  onChange={(event) => set({ type: event.target.value })}
                >
                  <option value="">Not set</option>
                  {withCurrent(WORKSTREAM_TYPES, form.type).map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                id="entity-phase"
                label="Phase"
                help="A workstream in Done stops counting as approaching its date."
              >
                <select
                  id="entity-phase"
                  className={fieldInput}
                  value={form.phase}
                  onChange={(event) => set({ phase: event.target.value })}
                >
                  <option value="">Not set</option>
                  {withCurrent(WORKSTREAM_PHASES, form.phase).map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>
              {(
                [
                  ["entity-owner", "Owner", "ownerId"],
                  ["entity-tpm", "Technical program manager", "tpmId"],
                  ["entity-sm", "Scrum master", "smId"],
                ] as const
              ).map(([id, label, field]) => (
                <Field key={id} id={id} label={label}>
                  <select
                    id={id}
                    className={fieldInput}
                    value={form[field]}
                    onChange={(event) =>
                      set({ [field]: event.target.value } as Partial<EntityForm>)
                    }
                  >
                    <option value="">Nobody</option>
                    {person(form[field]).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Field>
              ))}
              <Field id="entity-target" label="Target date" error={errors.targetDate}>
                <DayInput
                  id="entity-target"
                  className={fieldInput}
                  value={form.targetDate}
                  onChange={(day) => set({ targetDate: day })}
                />
              </Field>
              <Field
                id="entity-confidence"
                label="Confidence"
                help="From 0 to 1, for example 0.7."
                error={errors.confidence}
              >
                <input
                  id="entity-confidence"
                  inputMode="decimal"
                  className={fieldInput}
                  value={form.confidence}
                  onChange={(event) => set({ confidence: event.target.value })}
                />
              </Field>
            </div>
            <Field id="entity-summary" label="Summary">
              <textarea
                id="entity-summary"
                rows={2}
                className={textarea}
                value={form.summary}
                onChange={(event) => set({ summary: event.target.value })}
              />
            </Field>
          </fieldset>
        ) : null}

        {kind === "project" || kind === "pod" ? (
          // Only a project's and a pod's repositories are read by the Git sync.
          <Field
            id="entity-repos"
            label="Git repositories (optional)"
            error={errors.repos}
            help={
              kind === "pod"
                ? allowedRepos.length > 0
                  ? `One per line. Its projects list: ${allowedRepos.join(", ")}.`
                  : "One per line. A pod can only read repositories its projects list."
                : "One per line, for example acme/checkout-api. The Git sync reads these."
            }
          >
            <textarea
              id="entity-repos"
              rows={3}
              className={cn(textarea, "font-mono text-[13px]")}
              value={form.repos}
              onChange={(event) => set({ repos: event.target.value })}
            />
          </Field>
        ) : null}

        {failure ? (
          <p role="alert" className="rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red">
            {failure}
          </p>
        ) : null}
        <div className="flex items-center justify-end gap-2">
          {!hasChanges(update) && Object.keys(errors).length === 0 ? (
            <span className="mr-auto text-[12px] text-grey-secondary">
              Change something to save this {word}.
            </span>
          ) : null}
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={!ready || save.isPending}>
            {save.isPending ? "Saving…" : "Save changes"}
          </Pill>
        </div>
      </form>
    </FormDialog>
  );
}
