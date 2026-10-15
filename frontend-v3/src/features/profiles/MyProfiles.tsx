import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { isChecked } from "./profiles";

/**
 * The person's own profile in each pod they are in, to read: "In Payments Pod: QA and test".
 * An admin or the pod's scrum master sets it; nothing here changes it. Not drawn for someone
 * in no pod, or when it can't be read.
 */
export function MyProfiles({ enabled }: { enabled: boolean }) {
  const mine = useQuery({
    queryKey: ["profiles", "mine"],
    queryFn: () => apiClient.myProfiles(),
    enabled,
  });
  const profiles = mine.data ?? [];
  if (profiles.length === 0) return null;
  return (
    <section className="mt-5 border-t border-grey-border pt-4">
      <h3 className="text-[14px] font-extrabold">Your profile in each pod</h3>
      <ul className="mt-1 grid gap-0.5 text-[13px] text-grey-body">
        {profiles.map((item) => (
          <li key={item.pod_id}>
            In {item.pod_name}: <span className="font-bold text-ink">{item.label}</span>
            {isChecked(item) ? "" : " (from your role in the pod)"}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[12px] text-grey-secondary">
        It says what you bring to the pod, so the right person is asked. It grants no rights. An
        admin or the pod&rsquo;s scrum master sets it.
      </p>
    </section>
  );
}
