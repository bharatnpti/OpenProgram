import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { Rag, RollupFactorDto } from "../../api/schema";
import type { DeliveryKind } from "../../lib/useDeliverySelection";
import { rollupReasons, type ReasonsState } from "./rollupReasons";

/** A read that carries a node's rollup: project or workstream progress, or a pod's rollup. */
type RollupRead = {
  data?: { rag: Rag; factors: RollupFactorDto[]; source_names: Record<string, string> };
  isError: boolean;
};

/**
 * The reasons behind a project, workstream or pod panel's colour.
 *
 * Project and workstream progress already carry their rollup factors, so the
 * panel's own progress read is reused. A pod has no such read, so its rollup
 * is asked for here, under the same pod capabilities as its check-ins and
 * blockers. `rag` comes from the same response as the reasons, so the chip
 * and the reasons beside it always agree.
 */
export function useNodeReasons({
  kind,
  id,
  asOf,
  mayRead,
  progress,
}: {
  kind: DeliveryKind;
  id: string;
  asOf: string;
  mayRead: boolean;
  progress: RollupRead;
}): { rag: Rag | undefined; state: ReasonsState } {
  const podRollup = useQuery({
    queryKey: ["persona", "pod-rollup", id, asOf],
    queryFn: () => apiClient.podRollup(id, asOf),
    enabled: kind === "pod" && Boolean(id) && mayRead,
  });
  const read: RollupRead = kind === "pod" ? podRollup : progress;

  if (!mayRead) return { rag: undefined, state: { status: "denied" } };
  const data = read.data;
  if (data) {
    return {
      rag: data.rag,
      state: {
        status: "ready",
        reasons: rollupReasons(data.factors, (sourceId) => data.source_names[sourceId]),
      },
    };
  }
  return { rag: undefined, state: { status: read.isError ? "failed" : "loading" } };
}
