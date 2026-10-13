import type { ReactNode } from "react";
import { Navigate, useLocation, useNavigate, useParams } from "react-router-dom";

import type { DirectoryItemResponse } from "../api/schema";
import { useOwnTree, usePods, usePrograms, useProjects, useWorkstreams } from "../app/directory";
import { podPath } from "../app/links";
import { useRole } from "../app/role";
import { useDayWords } from "../app/viewingDate";
import { PanelState } from "../components/PanelState";
import { useAssistantSubject } from "../features/assistant/assistantContext";
import type { Kind } from "../features/delivery/factors";
import { Trail } from "../features/delivery/NodeBits";
import { ownProjects, trailOf } from "../features/delivery/ownTree";
import { PodPanel } from "../features/delivery/PodPanel";
import { ragSeverity } from "../lib/status";

/**
 * One pod, at `/pods/:podId` (D86): Delivery's pod panel, as it was, with a
 * plain picker. A bare `/pods` opens the person's own pod (else the first that
 * opens), or for a manager or admin the worst pod. Lane R builds the page.
 */
export function PodPage() {
  const { access } = useRole();
  return access.deliveryScope === "own" ? <OwnPod /> : <AnyPod />;
}

function useFinder() {
  const programs = usePrograms();
  const projects = useProjects();
  const workstreams = useWorkstreams();
  const pods = usePods();
  return (kind: Kind, id: string) =>
    ({
      program: programs.data,
      project: projects.data,
      workstream: workstreams.data,
      pod: pods.data,
    })[kind]?.find((item) => item.id === id);
}

/** A manager's or admin's: every pod. */
function AnyPod() {
  const { podId = "" } = useParams();
  const location = useLocation();
  const day = useDayWords();
  const pods = usePods();
  const find = useFinder();
  const list = [...(pods.data ?? [])].sort(
    (a, b) => ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
  );
  const pod = list.find((item) => item.id === podId);
  useAssistantSubject(pod ? { kind: "pod", name: pod.name } : null);
  if (!podId && list[0]) {
    return (
      <Navigate
        to={{ pathname: podPath(list[0].id), search: location.search, hash: location.hash }}
        replace
      />
    );
  }
  return (
    <PanelState
      isLoading={pods.isLoading}
      error={pods.error}
      isEmpty={list.length === 0}
      emptyText={
        day === "today" ? "No pods are configured yet." : `No pods were configured ${day}.`
      }
    >
      <Picker pods={list} current={podId} />
      {pod ? <PodPanel pod={pod} find={find} /> : <NotListed day={day} />}
    </PanelState>
  );
}

/** Everyone else's: the pods of their own part of the tree that open. */
function OwnPod() {
  const { podId = "" } = useParams();
  const location = useLocation();
  const day = useDayWords();
  const tree = useOwnTree();
  const pods = usePods();
  const find = useFinder();
  const listed = tree.data ? ownProjects(tree.data) : [];
  const open = listed
    .flatMap((project) => project.pods)
    .filter((pod, index, all) => pod.opens && all.findIndex((p) => p.id === pod.id) === index);
  const node = open.find((pod) => pod.id === podId);
  const item = node ? find("pod", node.id) : undefined;
  useAssistantSubject(node ? { kind: "pod", name: node.name } : null);
  const landing = open.find((pod) => pod.own) ?? open[0];
  if (!podId && landing) {
    return (
      <Navigate
        to={{ pathname: podPath(landing.id), search: location.search, hash: location.hash }}
        replace
      />
    );
  }
  const related = (id: string) =>
    listed
      .filter((project) => project.pods.some((pod) => pod.id === id))
      .map((project) => ({ ...project, noStatus: project.rag === null }));
  const picker = open
    .map((pod) => find("pod", pod.id))
    .filter((pod): pod is DirectoryItemResponse => Boolean(pod));
  let panel: ReactNode = <NotListed day={day} />;
  if (node && item && tree.data) {
    panel = (
      <PodPanel
        pod={item}
        find={find}
        own={{
          full: node.full,
          related: related(node.id),
          trail: <Trail steps={trailOf(tree.data, "pod", node.id)} />,
        }}
      />
    );
  }
  return (
    <PanelState
      isLoading={tree.isLoading || pods.isLoading}
      error={tree.error ?? pods.error}
      isEmpty={open.length === 0}
      emptyText={
        day === "today"
          ? "You are in no pod yet, so there is no pod to show."
          : `You were in no pod ${day}.`
      }
    >
      {picker.length > 1 ? <Picker pods={picker} current={podId} /> : null}
      {panel}
    </PanelState>
  );
}

function Picker({ pods, current }: { pods: DirectoryItemResponse[]; current: string }) {
  const navigate = useNavigate();
  return (
    <div className="mb-5">
      <label htmlFor="pod-picker" className="sr-only">
        Pod
      </label>
      <select
        id="pod-picker"
        className="h-10 max-w-full rounded-full border border-grey-border bg-white px-4 text-[14px] font-bold"
        value={current}
        onChange={(event) => navigate(podPath(event.target.value))}
      >
        {pods.some((pod) => pod.id === current) ? null : (
          <option value={current}>Pick a pod</option>
        )}
        {pods.map((pod) => (
          <option key={pod.id} value={pod.id}>
            {pod.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function NotListed({ day }: { day: string }) {
  return (
    <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
      That pod is not in the directory{day === "today" ? "" : ` ${day}`}. Pick one above.
    </p>
  );
}
