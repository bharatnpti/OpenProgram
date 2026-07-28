import { IdCard, PenLine, ShieldAlert, Trash2 } from "lucide-react";

import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import type { ConfigNodeResponse } from "../../api/schema";
import type { EntityKind } from "./adminTypes";

export function AdminTable({
  activeEntity,
  items,
  onEdit,
  onEscalation,
  onIdentity,
  onConsent,
  onDelete,
  deleteDisabled,
}: {
  activeEntity: EntityKind;
  items: ConfigNodeResponse[];
  onEdit: (node: ConfigNodeResponse) => void;
  onEscalation: (node: ConfigNodeResponse) => void;
  onIdentity: (node: ConfigNodeResponse) => void;
  onConsent: (node: ConfigNodeResponse) => void;
  onDelete: (node: ConfigNodeResponse) => void;
  deleteDisabled?: boolean;
}) {
  return (
    <div className="overflow-hidden rounded-3xl border border-grey-border bg-white">
      <div className="grid grid-cols-[150px_1fr_170px_auto] items-center gap-4 bg-grey-header px-5 py-3 text-[12px] font-bold uppercase tracking-wide text-grey-secondary">
        <span>ID</span>
        <span>Name</span>
        <span>Details</span>
        <span className="text-right">Actions</span>
      </div>
      {items.length === 0 ? (
        <div className="px-5 py-10 text-center text-[14px] text-grey-secondary">
          No records. Create a record or adjust the search filter.
        </div>
      ) : (
        items.map((node) => (
          <div
            key={node.id}
            className="grid grid-cols-[150px_1fr_170px_auto] items-center gap-4 border-t border-grey-fill px-5 py-4"
          >
            <span className="truncate font-mono text-[13px] text-grey-secondary">{node.id}</span>
            <div className="min-w-0">
              <div className="truncate text-[15px] font-bold">{node.name}</div>
              {node.description ? (
                <div className="truncate text-[13px] text-grey-secondary">
                  {node.description}
                </div>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-1">
              <IntegrationBadges node={node} />
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Pill variant="ghost" size="sm" onClick={() => onEdit(node)}>
                Edit
              </Pill>
              {activeEntity === "pods" && (
                <Pill variant="ghost" size="sm" onClick={() => onEscalation(node)}>
                  <ShieldAlert size={14} />
                  Escalation
                </Pill>
              )}
              {activeEntity === "members" && (
                <Pill variant="ghost" size="sm" onClick={() => onIdentity(node)}>
                  <IdCard size={14} />
                  Identity
                </Pill>
              )}
              {activeEntity === "members" && (
                <Pill variant="ghost" size="sm" onClick={() => onConsent(node)}>
                  <PenLine size={14} />
                  Write-back
                </Pill>
              )}
              <Pill
                variant="ghost"
                size="sm"
                className="border-rag-red-bg text-rag-red hover:bg-rag-red-bg"
                disabled={deleteDisabled}
                onClick={() => onDelete(node)}
              >
                <Trash2 size={14} />
                Delete
              </Pill>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function IntegrationBadges({ node }: { node: ConfigNodeResponse }) {
  const items = [
    node.code && `Code ${node.code}`,
    node.jira_project_key && `Jira ${node.jira_project_key}`,
    node.jira_base_jql && "Jira JQL",
    node.jira_board_id && `Board ${node.jira_board_id}`,
    node.jira_filter_jql && "Jira filter",
    ...(node.github_repos ?? []).map((repo) => `GitHub ${repo}`),
  ].filter((item): item is string => Boolean(item));

  if (items.length === 0) {
    return <span className="text-[13px] text-grey-secondary">—</span>;
  }
  return (
    <>
      {items.map((item) => (
        <RagChip key={item} tone="neutral">
          {item}
        </RagChip>
      ))}
    </>
  );
}
