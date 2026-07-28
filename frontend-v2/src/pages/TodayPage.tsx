import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { useRole } from "../app/role";
import { DeveloperToday } from "../features/today/DeveloperToday";
import { ManagerExecToday } from "../features/today/ManagerExecToday";
import { ProductOwnerToday } from "../features/today/ProductOwnerToday";
import { ScrumMasterToday } from "../features/today/ScrumMasterToday";
import { firstItemId } from "../lib/selection";
import { greetingFor, todayIso, todayKicker } from "../lib/today";

export function TodayPage() {
  const { role, roleLabel, user } = useRole();
  const asOf = todayIso();

  const programs = useQuery({
    queryKey: ["directory", "programs", asOf],
    queryFn: () => apiClient.programs(asOf),
  });
  const programId = firstItemId(programs.data ?? []);
  const program = programs.data?.find((item) => item.id === programId);

  const displayName = user?.name ?? user?.username ?? roleLabel;

  return (
    <div className="flex flex-col gap-6">
      <div className="animate-op-fade-up flex items-end justify-between gap-6">
        <div className="min-w-0">
          <div className="text-[13px] font-bold uppercase tracking-wide text-grey-secondary">
            {todayKicker(program?.name ?? null)}
          </div>
          <h1 className="mt-1.5 text-[40px] font-extrabold leading-[1.05]">
            {greetingFor(displayName)}
          </h1>
          <p className="mt-2.5 max-w-[640px] text-[18px] text-grey-secondary">
            {subtitleFor(role)}
          </p>
        </div>
      </div>

      {role === "dev" ? <DeveloperToday /> : null}
      {role === "sm" ? <ScrumMasterToday /> : null}
      {role === "po" ? <ProductOwnerToday /> : null}
      {role === "mgr" ? <ManagerExecToday role="mgr" /> : null}
      {role === "exec" ? <ManagerExecToday role="exec" /> : null}
      {role === "admin" ? <ManagerExecToday role="exec" /> : null}
    </div>
  );
}

function subtitleFor(role: string): string {
  switch (role) {
    case "dev":
      return "Confirm today's check-in and clear what's blocking you.";
    case "sm":
      return "Track who has checked in and how long blockers have been open.";
    case "po":
      return "See where your project stands and what needs your call.";
    default:
      return "A portfolio-wide read on delivery health, momentum, and what changed.";
  }
}
