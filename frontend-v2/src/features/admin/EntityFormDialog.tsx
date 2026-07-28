import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { TextArea, TextInput } from "../../components/ui/Field";
import type { ConfigNodeResponse } from "../../api/schema";
import { AdminSelect } from "./AdminSelect";
import { FormField } from "./FormField";
import {
  type EntityKind,
  type NodeFormValues,
  defaultNodeValues,
  editValuesFromNode,
  entityLabels,
  errorMessage,
  nodeSchema,
  saveNode,
  workstreamPhases,
  workstreamTypes,
} from "./adminTypes";

export function EntityFormDialog({
  open,
  onOpenChange,
  activeEntity,
  editing,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  activeEntity: EntityKind;
  editing: ConfigNodeResponse | null;
  onSaved: () => Promise<void>;
}) {
  const form = useForm<NodeFormValues>({
    resolver: zodResolver(nodeSchema),
    defaultValues: defaultNodeValues(),
  });

  useEffect(() => {
    if (!open) return;
    form.reset(editing ? editValuesFromNode(editing) : defaultNodeValues());
  }, [open, editing, form]);

  const saveMutation = useMutation({
    mutationFn: (values: NodeFormValues) => saveNode(activeEntity, values, editing),
    onSuccess: async () => {
      onOpenChange(false);
      await onSaved();
      toast.success("Configuration saved.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const label = entityLabels[activeEntity].slice(0, -1);

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit ${label}` : `Create ${label}`}
    >
      <p className="mb-4 text-[13px] text-grey-secondary">
        IDs are stable graph identifiers. Editing an existing ID is disabled.
      </p>
      <form
        className="flex flex-col gap-3.5"
        onSubmit={form.handleSubmit((values) => saveMutation.mutate(values))}
      >
        <FormField label="ID" htmlFor="node-id" error={form.formState.errors.id?.message}>
          <TextInput id="node-id" disabled={Boolean(editing)} {...form.register("id")} />
        </FormField>
        <FormField label="Name" htmlFor="node-name" error={form.formState.errors.name?.message}>
          <TextInput id="node-name" {...form.register("name")} />
        </FormField>
        <FormField label="Description" htmlFor="node-description">
          <TextArea id="node-description" {...form.register("description")} />
        </FormField>
        {(activeEntity === "projects" || editing?.code) && (
          <FormField label="Code" htmlFor="node-code">
            <TextInput id="node-code" {...form.register("code")} />
          </FormField>
        )}
        {activeEntity === "projects" && (
          <div className="flex flex-col gap-3 rounded-2xl bg-grey-fill p-4">
            <div className="text-[14px] font-bold">Project integrations</div>
            <div className="grid gap-3 md:grid-cols-2">
              <FormField label="Jira project key" htmlFor="project-jira-key">
                <TextInput id="project-jira-key" {...form.register("jira_project_key")} />
              </FormField>
              <FormField label="Jira board ID" htmlFor="project-jira-board">
                <TextInput id="project-jira-board" {...form.register("jira_board_id")} />
              </FormField>
            </div>
            <FormField label="Jira base JQL" htmlFor="project-jira-jql">
              <TextArea id="project-jira-jql" {...form.register("jira_base_jql")} />
            </FormField>
            <FormField label="GitHub repos" htmlFor="project-github-repos">
              <TextArea id="project-github-repos" {...form.register("github_repos")} />
            </FormField>
          </div>
        )}
        {activeEntity === "pods" && (
          <div className="flex flex-col gap-3 rounded-2xl bg-grey-fill p-4">
            <div className="text-[14px] font-bold">Pod integrations</div>
            <FormField label="Jira filter JQL" htmlFor="pod-jira-filter">
              <TextArea id="pod-jira-filter" {...form.register("jira_filter_jql")} />
            </FormField>
            <FormField label="GitHub repo subset" htmlFor="pod-github-repos">
              <TextArea id="pod-github-repos" {...form.register("github_repos")} />
            </FormField>
          </div>
        )}
        {activeEntity === "workstreams" && (
          <div className="flex flex-col gap-3 rounded-2xl bg-grey-fill p-4">
            <div className="text-[14px] font-bold">Workstream metadata</div>
            <div className="grid gap-3 md:grid-cols-2">
              <FormField label="Type" htmlFor="workstream-type">
                <AdminSelect id="workstream-type" {...form.register("type")}>
                  <option value="">Select type</option>
                  {workstreamTypes.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </AdminSelect>
              </FormField>
              <FormField label="Phase" htmlFor="workstream-phase">
                <AdminSelect id="workstream-phase" {...form.register("phase")}>
                  <option value="">Select phase</option>
                  {workstreamPhases.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </AdminSelect>
              </FormField>
              <FormField label="Owner ID" htmlFor="workstream-owner">
                <TextInput id="workstream-owner" {...form.register("owner_id")} />
              </FormField>
              <FormField label="TPM ID" htmlFor="workstream-tpm">
                <TextInput id="workstream-tpm" {...form.register("tpm_id")} />
              </FormField>
              <FormField label="SM ID" htmlFor="workstream-sm">
                <TextInput id="workstream-sm" {...form.register("sm_id")} />
              </FormField>
              <FormField label="Target date" htmlFor="workstream-target">
                <TextInput id="workstream-target" type="date" {...form.register("target_date")} />
              </FormField>
              <FormField label="Confidence" htmlFor="workstream-confidence">
                <TextInput
                  id="workstream-confidence"
                  inputMode="decimal"
                  placeholder="0.0 to 1.0"
                  {...form.register("confidence")}
                />
              </FormField>
            </div>
            <FormField label="Summary" htmlFor="workstream-summary">
              <TextArea id="workstream-summary" {...form.register("summary")} />
            </FormField>
          </div>
        )}
        <div className="mt-1 flex justify-end gap-3">
          <Pill variant="ghost" size="md" type="button" onClick={() => onOpenChange(false)}>
            Cancel
          </Pill>
          <Pill variant="primary" size="md" type="submit" disabled={saveMutation.isPending}>
            {saveMutation.isPending ? "Saving…" : "Save"}
          </Pill>
        </div>
      </form>
    </Modal>
  );
}
