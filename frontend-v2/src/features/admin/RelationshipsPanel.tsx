import type { ReactNode } from "react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { TextInput } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import type { ConfigNodeResponse } from "../../api/schema";
import { NodeSelect } from "./AdminSelect";
import { ConfirmDialog } from "./ConfirmDialog";
import { type ConfirmState, confirmUnlink, errorMessage } from "./adminTypes";

export function RelationshipsPanel({
  programs,
  projects,
  workstreams,
  pods,
  members,
  onChanged,
}: {
  programs: ConfigNodeResponse[];
  projects: ConfigNodeResponse[];
  workstreams: ConfigNodeResponse[];
  pods: ConfigNodeResponse[];
  members: ConfigNodeResponse[];
  onChanged: () => Promise<void>;
}) {
  const [confirm, setConfirm] = useState<ConfirmState>({ open: false });

  const [projectId, setProjectId] = useState("");
  const [programId, setProgramId] = useState("");
  const [podId, setPodId] = useState("");
  const [linkProjectId, setLinkProjectId] = useState("");
  const [workstreamProjectId, setWorkstreamProjectId] = useState("");
  const [projectWorkstreamId, setProjectWorkstreamId] = useState("");
  const [workstreamPodId, setWorkstreamPodId] = useState("");
  const [podWorkstreamId, setPodWorkstreamId] = useState("");
  const [taskWorkstreamId, setTaskWorkstreamId] = useState("");
  const [workstreamTaskId, setWorkstreamTaskId] = useState("");
  const [memberId, setMemberId] = useState("");
  const [memberRole, setMemberRole] = useState("developer");
  const [taskMemberId, setTaskMemberId] = useState("");
  const [taskId, setTaskId] = useState("");

  const run = async (action: () => Promise<unknown>, message: string) => {
    try {
      await action();
      await onChanged();
      toast.success(message);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-[18px] font-bold">Links &amp; assignments</h2>
        <p className="mt-1 text-[13px] text-grey-secondary">
          Maintain graph relationships without leaving empty or ambiguous mutations.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <LinkForm
          title="Project to Program"
          canSubmit={Boolean(projectId && programId)}
          onSubmit={() =>
            void run(
              () => apiClient.linkProjectProgram(projectId, { program_id: programId }),
              "Linked project to program.",
            )
          }
          onUnlink={() =>
            confirmUnlink(setConfirm, "Unlink project from program?", () =>
              void run(
                () => apiClient.unlinkProjectProgram(projectId, programId),
                "Unlinked project from program.",
              ),
            )
          }
        >
          <NodeSelect
            value={projectId}
            onChange={setProjectId}
            items={projects}
            placeholder="Select project"
          />
          <NodeSelect
            value={programId}
            onChange={setProgramId}
            items={programs}
            placeholder="Select program"
          />
        </LinkForm>

        <LinkForm
          title="Pod to Project"
          canSubmit={Boolean(podId && linkProjectId)}
          onSubmit={() =>
            void run(() => apiClient.linkPodProject(podId, linkProjectId), "Linked pod to project.")
          }
          onUnlink={() =>
            confirmUnlink(setConfirm, "Unlink pod from project?", () =>
              void run(
                () => apiClient.unlinkPodProject(podId, linkProjectId),
                "Unlinked pod from project.",
              ),
            )
          }
        >
          <NodeSelect value={podId} onChange={setPodId} items={pods} placeholder="Select pod" />
          <NodeSelect
            value={linkProjectId}
            onChange={setLinkProjectId}
            items={projects}
            placeholder="Select project"
          />
        </LinkForm>

        <LinkForm
          title="Project to Workstream"
          canSubmit={Boolean(workstreamProjectId && projectWorkstreamId)}
          onSubmit={() =>
            void run(
              () => apiClient.linkProjectWorkstream(workstreamProjectId, projectWorkstreamId),
              "Linked project to workstream.",
            )
          }
          onUnlink={() =>
            confirmUnlink(setConfirm, "Unlink workstream from project?", () =>
              void run(
                () => apiClient.unlinkProjectWorkstream(workstreamProjectId, projectWorkstreamId),
                "Unlinked workstream from project.",
              ),
            )
          }
        >
          <NodeSelect
            value={workstreamProjectId}
            onChange={setWorkstreamProjectId}
            items={projects}
            placeholder="Select project"
          />
          <NodeSelect
            value={projectWorkstreamId}
            onChange={setProjectWorkstreamId}
            items={workstreams}
            placeholder="Select workstream"
          />
        </LinkForm>

        <LinkForm
          title="Pod to Workstream"
          canSubmit={Boolean(workstreamPodId && podWorkstreamId)}
          onSubmit={() =>
            void run(
              () => apiClient.linkPodWorkstream(workstreamPodId, podWorkstreamId),
              "Linked pod to workstream.",
            )
          }
          onUnlink={() =>
            confirmUnlink(setConfirm, "Unlink pod from workstream?", () =>
              void run(
                () => apiClient.unlinkPodWorkstream(workstreamPodId, podWorkstreamId),
                "Unlinked pod from workstream.",
              ),
            )
          }
        >
          <NodeSelect
            value={workstreamPodId}
            onChange={setWorkstreamPodId}
            items={pods}
            placeholder="Select pod"
          />
          <NodeSelect
            value={podWorkstreamId}
            onChange={setPodWorkstreamId}
            items={workstreams}
            placeholder="Select workstream"
          />
        </LinkForm>

        <LinkForm
          title="Pod to Member"
          canSubmit={Boolean(podId && memberId && memberRole.trim())}
          onSubmit={() =>
            void run(
              () => apiClient.linkPodMember(podId, memberId, { role: memberRole }),
              "Linked member to pod.",
            )
          }
          onUnlink={() =>
            confirmUnlink(setConfirm, "Unlink member from pod?", () =>
              void run(() => apiClient.unlinkPodMember(podId, memberId), "Unlinked member from pod."),
            )
          }
        >
          <NodeSelect value={podId} onChange={setPodId} items={pods} placeholder="Select pod" />
          <NodeSelect
            value={memberId}
            onChange={setMemberId}
            items={members}
            placeholder="Select member"
          />
          <TextInput
            value={memberRole}
            onChange={(event) => setMemberRole(event.target.value)}
            placeholder="Role in pod"
          />
        </LinkForm>

        <LinkForm
          title="Workstream to Task"
          canSubmit={Boolean(taskWorkstreamId && workstreamTaskId.trim())}
          onSubmit={() =>
            void run(
              () => apiClient.linkWorkstreamTask(taskWorkstreamId, workstreamTaskId),
              "Linked task to workstream.",
            )
          }
          onUnlink={() =>
            confirmUnlink(setConfirm, "Unlink task from workstream?", () =>
              void run(
                () => apiClient.unlinkWorkstreamTask(taskWorkstreamId, workstreamTaskId),
                "Unlinked task from workstream.",
              ),
            )
          }
        >
          <NodeSelect
            value={taskWorkstreamId}
            onChange={setTaskWorkstreamId}
            items={workstreams}
            placeholder="Select workstream"
          />
          <TextInput
            placeholder="Task ID"
            value={workstreamTaskId}
            onChange={(event) => setWorkstreamTaskId(event.target.value)}
          />
        </LinkForm>

        <LinkForm
          title="Member to Task"
          canSubmit={Boolean(taskMemberId && taskId.trim())}
          onSubmit={() =>
            void run(
              () => apiClient.assignMemberTask(taskMemberId, { task_id: taskId }),
              "Assigned task to member.",
            )
          }
          onUnlink={() =>
            confirmUnlink(setConfirm, "Unassign task from member?", () =>
              void run(
                () => apiClient.unassignMemberTask(taskMemberId, taskId),
                "Unassigned task from member.",
              ),
            )
          }
        >
          <NodeSelect
            value={taskMemberId}
            onChange={setTaskMemberId}
            items={members}
            placeholder="Select member"
          />
          <TextInput
            placeholder="Task ID"
            value={taskId}
            onChange={(event) => setTaskId(event.target.value)}
          />
        </LinkForm>
      </div>

      <ConfirmDialog state={confirm} onOpenChange={(open) => !open && setConfirm({ open: false })} />
    </div>
  );
}

function LinkForm({
  title,
  children,
  canSubmit,
  onSubmit,
  onUnlink,
}: {
  title: string;
  children: ReactNode;
  canSubmit: boolean;
  onSubmit: () => void;
  onUnlink: () => void;
}) {
  return (
    <section className="flex flex-col gap-3 rounded-3xl border border-grey-border bg-white p-5">
      <div className="text-[15px] font-bold">{title}</div>
      <div className="flex flex-col gap-2.5">{children}</div>
      <div className="flex gap-2.5">
        <Pill variant="primary" size="sm" disabled={!canSubmit} onClick={onSubmit}>
          Link
        </Pill>
        <Pill variant="ghost" size="sm" disabled={!canSubmit} onClick={onUnlink}>
          Unlink
        </Pill>
      </div>
    </section>
  );
}
