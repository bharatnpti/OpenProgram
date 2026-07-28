import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { DeliveryDetail } from "../features/delivery/DeliveryDetail";
import { DeliveryNavigator } from "../features/delivery/DeliveryNavigator";
import { firstItemId } from "../lib/selection";
import { todayIso } from "../lib/today";
import { useDeliverySelection } from "../lib/useDeliverySelection";

export function DeliveryPage() {
  const asOf = todayIso();

  const programs = useQuery({
    queryKey: ["directory", "programs", asOf],
    queryFn: () => apiClient.programs(asOf),
  });
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const workstreams = useQuery({
    queryKey: ["directory", "workstreams", asOf],
    queryFn: () => apiClient.workstreams(asOf),
  });
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });

  const defaultProgramId = firstItemId(programs.data ?? []);
  const { kind, id, select } = useDeliverySelection(defaultProgramId);

  const lists = {
    programs: programs.data ?? [],
    projects: projects.data ?? [],
    workstreams: workstreams.data ?? [],
    pods: pods.data ?? [],
  };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-[28px] font-extrabold">Delivery</h1>
        <p className="mt-1 text-[15px] text-grey-secondary">
          Explore the program graph and drill into any project, workstream, or pod.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[360px_1fr]">
        <DeliveryNavigator
          programs={lists.programs}
          projects={lists.projects}
          workstreams={lists.workstreams}
          pods={lists.pods}
          selection={{ kind, id }}
          onSelect={select}
        />
        <DeliveryDetail selection={{ kind, id }} lists={lists} asOf={asOf} onSelect={select} />
      </div>
    </div>
  );
}
