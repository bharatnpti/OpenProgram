import { Link, useLocation, useNavigate } from "react-router-dom";

import { useRole } from "../../app/role";
import { cn } from "../../lib/utils";
import { VERDICT_LABELS } from "../../lib/status";
import type { HubPlace, HubTab } from "./hubPaths";
import { hubHref, PODS_AS_CHIPS, type PodChoice } from "./hubTabs";
import { VerdictDot } from "./VerdictDot";

/**
 * The pod bar: the level within a project. "Whole project" and each pod of
 * it, worst first, each with its date verdict where the reader has it; past a
 * few, the rest are one "More pods" pick. A pick keeps the tab, the viewing
 * day and the release. On a phone it is one "Pod" select, so the page never
 * scrolls sideways. A project with no pod the person can open has no bar.
 */
export function PodBar({
  place,
  tab,
  pods,
}: {
  place: Exclude<HubPlace, { level: "all" }>;
  tab: HubTab;
  pods: PodChoice[];
}) {
  const { access } = useRole();
  const { search } = useLocation();
  const navigate = useNavigate();
  if (pods.length === 0 || access.hub.tabs.pod.length === 0) return null;
  const from = { place, tab };
  const projectId = place.projectId;
  const currentPod = place.level === "pod" ? place.podId : null;
  const whole = hubHref(access.hub.tabs, from, { level: "project", projectId }, tab, search);
  const podHref = (podId: string) =>
    hubHref(access.hub.tabs, from, { level: "pod", projectId, podId }, tab, search);
  const chips = pods.slice(0, PODS_AS_CHIPS);
  // The pod on screen is always a chip, even when it sorts past the first few.
  const picked = pods.find((pod) => pod.id === currentPod);
  if (picked && !chips.includes(picked)) chips.push(picked);
  const more = pods.filter((pod) => !chips.includes(pod));
  const dated = access.hub.bulk;
  const words = (pod: PodChoice) =>
    pod.total !== null && pod.total <= 0
      ? "Nothing in scope"
      : pod.verdict
        ? VERDICT_LABELS[pod.verdict]
        : null;
  const chip = (current: boolean) =>
    cn(
      "inline-flex min-h-9 items-center gap-2 rounded-full border px-3 text-[13px] font-bold no-underline",
      current
        ? "border-ink bg-ink text-white"
        : "border-grey-border bg-(--op-viz-surface) text-grey-body hover:bg-grey-fill hover:text-ink",
    );

  return (
    <div className="mb-6">
      {/* A phone: one select, so the page never scrolls sideways. */}
      <div className="flex items-center gap-2 sm:hidden">
        <label htmlFor="hub-pod" className="text-[13px] font-bold">
          Pod
        </label>
        <select
          id="hub-pod"
          value={currentPod ?? ""}
          onChange={(event) => navigate(event.target.value ? podHref(event.target.value) : whole)}
          className="h-10 min-w-0 flex-1 rounded-full border border-grey-border bg-(--op-viz-surface) px-4 text-[14px] font-bold text-ink"
        >
          <option value="">Whole project</option>
          {pods.map((pod) => {
            const said = dated ? words(pod) : null;
            return (
              <option key={pod.id} value={pod.id}>
                {said ? `${pod.name} · ${said}` : pod.name}
              </option>
            );
          })}
        </select>
      </div>
      <nav aria-label="Pod" className="hidden flex-wrap items-center gap-2 sm:flex">
        <span className="text-[13px] font-bold text-grey-secondary">Pod:</span>
        <Link
          to={whole}
          aria-current={currentPod === null ? "page" : undefined}
          className={chip(currentPod === null)}
        >
          Whole project
        </Link>
        {chips.map((pod) => {
          const said = dated ? words(pod) : null;
          const current = pod.id === currentPod;
          return (
            <Link
              key={pod.id}
              to={podHref(pod.id)}
              aria-current={current ? "page" : undefined}
              className={chip(current)}
            >
              {dated ? <VerdictDot verdict={pod.verdict} /> : null}
              {pod.name}
              {said ? (
                <span
                  className={cn("font-normal", current ? "text-white/80" : "text-grey-secondary")}
                >
                  {said}
                </span>
              ) : null}
            </Link>
          );
        })}
        {more.length > 0 ? (
          <>
            <label htmlFor="hub-more-pods" className="sr-only">
              More pods
            </label>
            <select
              id="hub-more-pods"
              value=""
              onChange={(event) => {
                if (event.target.value) navigate(podHref(event.target.value));
              }}
              className="h-9 rounded-full border border-grey-border bg-(--op-viz-surface) px-3 text-[13px] font-bold text-grey-body"
            >
              <option value="">More pods ({more.length})</option>
              {more.map((pod) => {
                const said = dated ? words(pod) : null;
                return (
                  <option key={pod.id} value={pod.id}>
                    {said ? `${pod.name} · ${said}` : pod.name}
                  </option>
                );
              })}
            </select>
          </>
        ) : null}
      </nav>
    </div>
  );
}
