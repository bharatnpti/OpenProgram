import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { DirectoryItemResponse } from "../api/schema";
import {
  podsOfPerson,
  useMemberId,
  useOwnTree,
  usePods,
  usePrograms,
  useProjects,
  useWorkstreams,
} from "../app/directory";
import { podPath } from "../app/links";
import { useRole } from "../app/role";
import { useDayWords } from "../app/viewingDate";
import { PanelState } from "../components/PanelState";
import { RagBadge } from "../components/ui/Bits";
import { useAssistantSubject } from "../features/assistant/assistantContext";
import { PAGE_SURFACE } from "../features/coordination/pageSurface";
import { PodDateStrips } from "../features/delivery/DeliveryStrips";
import { PodTimeline } from "../features/delivery/DeliveryTimeline";
import { reasonLine, type Kind } from "../features/delivery/factors";
import { FactorsPanel, Related, Trail } from "../features/delivery/NodeBits";
import { ownProjects, trailOf } from "../features/delivery/ownTree";
import { BlockersAndWaits } from "../features/pods/BlockersAndWaits";
import { NudgeList } from "../features/pods/NudgeList";
import { PodSwitcher } from "../features/pods/PodSwitcher";
import {
  defaultPod,
  podLine,
  podOptions,
  recentPodsKey,
  scrumMasterOf,
} from "../features/pods/podWords";
import { TodayStatus } from "../features/pods/TodayStatus";
import { PodPeoplePanel } from "../features/profiles/PodPeople";
import {
  readRecent,
  remembered,
  writeRecent,
  type ProjectOption,
} from "../features/project/projectChoice";
import { useReportAccess } from "../features/reports/useReportAccess";
import { readState } from "../lib/readState";
import { ragSeverity } from "../lib/status";

/*
 * One pod, at `/pods/:podId` (D86): the Delivery pod panel and the scrum
 * master's morning on one page. Top to bottom: the pod (its switcher, colour
 * and reason, scrum master, members and the day's counts); its dates and its
 * timeline row; today's status of its tasks; who needs a nudge (the roles that
 * read pod check-ins only, D87); blockers and waits; why it has its colour;
 * its people. A reader of its dates only gets the first two. A bare `/pods`
 * opens the worst of the person's own pods, else the one opened last, else the
 * worst pod; the URL always wins.
 */
export function PodPage() {
  const { access } = useRole();
  return (
    <div className={PAGE_SURFACE}>{access.deliveryScope === "own" ? <OwnPod /> : <AnyPod />}</div>
  );
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

/** The pods opened lately in this browser, for this person; never sent anywhere. */
function useRecentPods(podId: string) {
  const memberId = useMemberId();
  const key = recentPodsKey(memberId);
  const [recent, setRecent] = useState(() => readRecent(() => localStorage, key));
  useEffect(() => {
    if (!podId) return;
    setRecent((current) => {
      const next = remembered(current, podId);
      writeRecent(() => localStorage, key, next);
      return next;
    });
  }, [key, podId]);
  return recent;
}

/** The projects, worst first: the pods' headings in the switcher. */
function useProjectHeadings() {
  const projects = useProjects();
  return useMemo(
    () =>
      [...(projects.data ?? [])]
        .sort((a, b) => ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name))
        .map((project) => ({ id: project.id, name: project.name })),
    [projects.data],
  );
}

/** A manager's or admin's: every pod. */
function AnyPod() {
  const { podId = "" } = useParams();
  const location = useLocation();
  const day = useDayWords();
  const memberId = useMemberId();
  const pods = usePods();
  const find = useFinder();
  const recent = useRecentPods(podId);
  const headings = useProjectHeadings();
  const list = pods.data ?? [];
  const own = podsOfPerson(list, memberId);
  const options = podOptions(list, new Set(own.own ? own.pods.map((pod) => pod.id) : []));
  const pod = list.find((item) => item.id === podId);
  useAssistantSubject(pod ? { kind: "pod", name: pod.name } : null);
  const landing = defaultPod(options, recent);
  if (!podId && landing) {
    return (
      <Navigate
        to={{ pathname: podPath(landing), search: location.search, hash: location.hash }}
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
      {pod ? (
        <PodView
          pod={pod}
          full
          switcher={
            <PodSwitcher
              current={{ id: pod.id, name: pod.name }}
              options={options}
              projects={headings}
              recent={recent}
            />
          }
          related={
            <Related
              label="Projects"
              kind="project"
              items={pod.project_ids.map((id) => find("project", id))}
            />
          }
        />
      ) : (
        <NotListed day={day} />
      )}
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
  const recent = useRecentPods(podId);
  const headings = useProjectHeadings();
  const listed = tree.data ? ownProjects(tree.data) : [];
  const open = listed
    .flatMap((project) => project.pods)
    .filter((pod, index, all) => pod.opens && all.findIndex((p) => p.id === pod.id) === index);
  const node = open.find((pod) => pod.id === podId);
  const item = node ? find("pod", node.id) : undefined;
  useAssistantSubject(node ? { kind: "pod", name: node.name } : null);
  const items = open
    .map((pod) => find("pod", pod.id))
    .filter((pod): pod is DirectoryItemResponse => Boolean(pod));
  // The colour the person's tree gives each pod (none for a pod they read no colour of).
  const options: ProjectOption[] = podOptions(
    items,
    new Set(open.filter((pod) => pod.own).map((pod) => pod.id)),
    (pod) => open.find((p) => p.id === pod.id)?.rag ?? null,
  );
  const landing = defaultPod(options, recent);
  if (!podId && landing) {
    return (
      <Navigate
        to={{ pathname: podPath(landing), search: location.search, hash: location.hash }}
        replace
      />
    );
  }
  let page: ReactNode = <NotListed day={day} />;
  if (node && item && tree.data) {
    const related = listed
      .filter((project) => project.pods.some((pod) => pod.id === node.id))
      .map((project) => ({ ...project, noStatus: project.rag === null }));
    page = (
      <PodView
        pod={item}
        full={node.full}
        datesReadable
        trail={<Trail steps={trailOf(tree.data, "pod", node.id)} />}
        switcher={
          <PodSwitcher
            current={{ id: item.id, name: item.name }}
            options={options}
            projects={headings}
            recent={recent}
          />
        }
        related={<Related label="Projects" kind="project" items={related} />}
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
      {page}
    </PanelState>
  );
}

/**
 * The page of one pod. `full`: the reader reads the pod's detail (its check-ins,
 * blockers and tasks: a scrum master's own pod, a manager, an admin, a
 * developer's own pod); without it, its dates and colour only.
 */
function PodView({
  pod,
  full,
  datesReadable,
  trail,
  switcher,
  related,
}: {
  pod: DirectoryItemResponse;
  full: boolean;
  /** Its dates are the reader's from their own part of the tree, whatever the role. */
  datesReadable?: boolean;
  trail?: ReactNode;
  switcher: ReactNode;
  related: ReactNode;
}) {
  const { access } = useRole();
  const { readPodDelivery } = useReportAccess();
  const day = useDayWords();
  const rollup = useQuery({
    queryKey: ["pod", pod.id, "rollup"],
    queryFn: () => apiClient.podRollup(pod.id),
    enabled: full,
  });
  const checkins = useQuery({
    queryKey: ["pod", pod.id, "checkins"],
    queryFn: () => apiClient.podCheckins(pod.id),
    enabled: full,
  });
  const blockers = useQuery({
    queryKey: ["pod", pod.id, "blockers"],
    queryFn: () => apiClient.podBlockers(pod.id),
    enabled: full,
  });
  const c = checkins.data;
  const counted = c ? c.confirmed + c.partial + c.stale + c.missing : null;
  const rag = rollup.data?.rag ?? pod.rag;
  const reason = rollup.data
    ? reasonLine(rollup.data.factors, rollup.data.source_names, rollup.data.rag)
    : null;
  const read = full ? readState(rollup) : undefined;

  return (
    <>
      <header className="mb-5">
        {trail}
        <p className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">Pod</p>
        <div className="mt-1 flex min-w-0 flex-wrap items-center gap-3">
          {switcher}
          <RagBadge rag={rag} />
        </div>
        {full && !read?.error ? (
          <p className="mt-1 text-[15px] text-grey-body">
            {read?.isLoading
              ? "Loading…"
              : (reason ??
                (rag && rag !== "unknown"
                  ? "No reason recorded."
                  : "Nothing recorded for it yet."))}
          </p>
        ) : null}
        <p className="mt-2 text-[13px] text-grey-body">
          {podLine({
            scrumMaster: scrumMasterOf(pod),
            members: pod.member_ids.length,
            replied: c ? c.confirmed : null,
            partly: c?.partial ?? 0,
            counted,
            day,
            blockers: blockers.data ? blockers.data.blockers.length : null,
          })}
        </p>
        <div className="mt-3">{related}</div>
      </header>
      <div className="mb-5 grid grid-cols-[minmax(0,1fr)] gap-3">
        {/* Every pod that opens shows its dates: that is all a dates-only reader has. */}
        <PodDateStrips pod={pod} readable={datesReadable ? true : undefined} />
        {/* Its own timeline row: done, the likely finish and what changed. */}
        <PodTimeline pod={pod} readable={datesReadable ? true : readPodDelivery} />
      </div>
      {full ? (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          <section id="status" aria-label="Today's status">
            <TodayStatus podId={pod.id} day={day} />
          </section>
          {c?.reads_ladder ? (
            <section id="checkins" aria-label="Who needs a nudge">
              <NudgeList read={checkins} day={day} />
            </section>
          ) : null}
          <section id="blockers" aria-label="Blockers and waits">
            <BlockersAndWaits
              podId={pod.id}
              memberIds={pod.member_ids}
              waits={access.coordination.board}
            />
          </section>
          {rollup.data ? (
            <FactorsPanel
              title="Why this colour"
              factors={rollup.data.factors}
              names={rollup.data.source_names}
            />
          ) : null}
          <section id="people" aria-label="People">
            <PodPeoplePanel podId={pod.id} />
          </section>
        </div>
      ) : null}
    </>
  );
}

function NotListed({ day }: { day: string }) {
  return (
    <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
      That pod is not in the directory{day === "today" ? "" : ` ${day}`}. Pick one from the list.
    </p>
  );
}
