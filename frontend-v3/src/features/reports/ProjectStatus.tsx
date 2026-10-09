import type { DirectoryItemResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { RagChip } from "../../components/ui/RagChip";
import { useDelivery } from "../overall/queries";
import { projectStatus } from "./projectStatusWords";

/**
 * The one status on a project's card in Reports. For a role that reads the
 * project's delivery it is the delivery verdict, the same words and colour as
 * the date line under it (which then leaves its own chip out); for any other it
 * is the project's colour in words. Never both, never the enum.
 */
export function ProjectStatus({ project }: { project: DirectoryItemResponse }) {
  const { canReadProjectProgress } = useRole();
  return canReadProjectProgress ? (
    <DeliveryStatus project={project} />
  ) : (
    <Status status={projectStatus(null, project.rag)} />
  );
}

/** The delivery read is the strip's own query (one request), so the two never disagree. */
function DeliveryStatus({ project }: { project: DirectoryItemResponse }) {
  const { query } = useDelivery(project.id);
  return (
    <Status
      status={projectStatus({ scope: query.data?.project, reading: query.isLoading }, project.rag)}
    />
  );
}

function Status({ status }: { status: ReturnType<typeof projectStatus> }) {
  return status ? (
    <RagChip tone={status.tone} className="h-6 px-2.5 text-[12px]">
      {status.label}
    </RagChip>
  ) : null;
}
