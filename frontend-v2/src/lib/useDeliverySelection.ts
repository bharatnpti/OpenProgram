import { useNavigate, useParams } from "react-router-dom";

export type DeliveryKind = "program" | "project" | "workstream" | "pod";

export function useDeliverySelection(defaultProgramId: string | undefined) {
  const params = useParams<{ kind?: string; id?: string }>();
  const navigate = useNavigate();

  const kind = (params.kind as DeliveryKind | undefined) ?? "program";
  const id = params.id ?? defaultProgramId ?? "";

  function select(nextKind: DeliveryKind, nextId: string) {
    navigate(`/delivery/${nextKind}/${encodeURIComponent(nextId)}`);
  }

  return { kind, id, select };
}
