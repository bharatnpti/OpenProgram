import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";

const KIND_LABEL: Record<string, string> = {
  daily_pod: "Daily pod",
  weekly_project: "Weekly project",
  exec: "Exec",
};

export function BriefsColumn() {
  const briefs = useQuery({
    queryKey: ["persona", "briefs"],
    queryFn: () => apiClient.personaBriefs(undefined, 20),
    staleTime: 5 * 60_000,
  });

  return (
    <div className="flex flex-col gap-5">
      <h2 className="text-[18px] font-bold">Briefs</h2>
      {(briefs.data?.briefs ?? []).map((brief) => (
        <Card key={`${brief.kind}-${brief.scope_id}-${brief.generated_at}`} padding="p-6">
          <div className="text-xs font-bold uppercase tracking-wide text-magenta">
            {KIND_LABEL[brief.kind] ?? brief.kind}
          </div>
          <h3 className="mt-2 text-[17px] font-bold">{brief.title}</h3>
          <div className="mt-1 text-xs text-grey-secondary">
            {new Date(brief.generated_at).toLocaleDateString()}
          </div>
          <p className="mt-2 text-[14px] leading-relaxed text-grey-body">{brief.body}</p>
        </Card>
      ))}
      {briefs.data && briefs.data.briefs.length === 0 ? (
        <p className="text-sm text-grey-secondary">No briefs generated yet.</p>
      ) : null}
    </div>
  );
}
