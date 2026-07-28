import { useMutation } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { Wand2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { TextInput } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import type { ConfigNodeResponse } from "../../api/schema";
import { AdminTable } from "./AdminTable";
import { ConfirmDialog } from "./ConfirmDialog";
import { EntityFormDialog } from "./EntityFormDialog";
import { EscalationContactsDialog } from "./EscalationContactsDialog";
import { IdentityLinkDialog } from "./IdentityLinkDialog";
import { WritebackConsentDialog } from "./WritebackConsentDialog";
import {
  type ConfirmState,
  type EntityKind,
  deleteNode,
  entityKinds,
  entityLabels,
  errorMessage,
  filterNodes,
} from "./adminTypes";

export function EntitiesPanel({
  entities,
  entityQueries,
  onChanged,
}: {
  entities: Record<EntityKind, ConfigNodeResponse[]>;
  entityQueries: Record<EntityKind, UseQueryResult<ConfigNodeResponse[], Error>>;
  onChanged: () => Promise<void>;
}) {
  const [activeEntity, setActiveEntity] = useState<EntityKind>("programs");
  const [entityQuery, setEntityQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigNodeResponse | null>(null);
  const [escalationPod, setEscalationPod] = useState<ConfigNodeResponse | null>(null);
  const [identityMember, setIdentityMember] = useState<ConfigNodeResponse | null>(null);
  const [consentMember, setConsentMember] = useState<ConfigNodeResponse | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState>({ open: false });

  const deleteMutation = useMutation({
    mutationFn: ({ kind, id }: { kind: EntityKind; id: string }) => deleteNode(kind, id),
    onSuccess: async () => {
      await onChanged();
      toast.success("Record deleted.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const autoMatchMutation = useMutation({
    mutationFn: () => apiClient.autoMatchConfigIdentityLinks(),
    onSuccess: async (result) => {
      await onChanged();
      if (result.updated_count === 0) {
        toast.info("No identity links needed auto-matching.");
      } else {
        toast.success(
          `Auto-matched ${result.updated_count} member${result.updated_count === 1 ? "" : "s"} from the directory.`,
        );
      }
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const activeQuery = entityQueries[activeEntity];
  const currentEntities = useMemo(
    () => filterNodes(entities[activeEntity] ?? [], entityQuery),
    [entities, activeEntity, entityQuery],
  );

  function openCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function openEdit(node: ConfigNodeResponse) {
    setEditing(node);
    setDialogOpen(true);
  }

  function confirmDelete(kind: EntityKind, node: ConfigNodeResponse) {
    setConfirm({
      open: true,
      title: `Delete ${node.name}?`,
      description: `This removes ${node.id} from ${entityLabels[kind]}. Related links may also become invalid.`,
      confirmLabel: "Delete",
      destructive: true,
      onConfirm: () => deleteMutation.mutate({ kind, id: node.id }),
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {entityKinds.map((kind) => (
            <Pill
              key={kind}
              variant={activeEntity === kind ? "primary" : "ghost"}
              size="sm"
              onClick={() => {
                setActiveEntity(kind);
                setEntityQuery("");
              }}
            >
              {entityLabels[kind]}
            </Pill>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {activeEntity === "members" && (
            <Pill
              variant="ghost"
              size="sm"
              disabled={autoMatchMutation.isPending}
              onClick={() => autoMatchMutation.mutate()}
            >
              <Wand2 size={14} />
              {autoMatchMutation.isPending ? "Matching…" : "Auto-match"}
            </Pill>
          )}
          <Pill variant="dark" size="sm" onClick={openCreate}>
            Add {entityLabels[activeEntity].slice(0, -1)}
          </Pill>
        </div>
      </div>

      <TextInput
        value={entityQuery}
        onChange={(event) => setEntityQuery(event.target.value)}
        placeholder={`Search ${entityLabels[activeEntity].toLowerCase()}`}
        className="max-w-sm"
      />

      {activeQuery.isLoading ? (
        <p className="text-[14px] text-grey-secondary">Loading…</p>
      ) : activeQuery.isError ? (
        <p className="text-[14px] text-rag-red">{errorMessage(activeQuery.error)}</p>
      ) : (
        <AdminTable
          activeEntity={activeEntity}
          items={currentEntities}
          onEdit={openEdit}
          onEscalation={setEscalationPod}
          onIdentity={setIdentityMember}
          onConsent={setConsentMember}
          onDelete={(node) => confirmDelete(activeEntity, node)}
          deleteDisabled={deleteMutation.isPending}
        />
      )}

      <EntityFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        activeEntity={activeEntity}
        editing={editing}
        onSaved={onChanged}
      />

      <EscalationContactsDialog pod={escalationPod} onClose={() => setEscalationPod(null)} />
      <IdentityLinkDialog member={identityMember} onClose={() => setIdentityMember(null)} />
      <WritebackConsentDialog member={consentMember} onClose={() => setConsentMember(null)} />

      <ConfirmDialog state={confirm} onOpenChange={(open) => !open && setConfirm({ open: false })} />
    </div>
  );
}
