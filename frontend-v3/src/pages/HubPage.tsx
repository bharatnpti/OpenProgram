import { lazy, Suspense, useEffect, type ComponentType, type ReactNode } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";

import { useOwnTree, useProjects } from "../app/directory";
import { useRole } from "../app/role";
import { PastDaySaidContext, useDayWords, useViewingDate } from "../app/viewingDate";
import { PanelState } from "../components/PanelState";
import { useAssistantSubject } from "../features/assistant/assistantContext";
import { ownProjects } from "../features/delivery/ownTree";
import { HubHeader } from "../features/hub/HubHeader";
import {
  isHubTab,
  placePath,
  type HubLevel,
  type HubPlace,
  type HubTab,
} from "../features/hub/hubPaths";
import { HubTabs } from "../features/hub/HubTabs.tsx";
import { hubHref } from "../features/hub/hubTabs";
import { PodBar } from "../features/hub/PodBar";
import {
  useDirectoryPod,
  useHubBadges,
  useHubProject,
  usePodChoices,
  useReleaseChoice,
} from "../features/hub/useHub";
import { useReadsReadiness } from "../features/overall/queries";
import { ReleaseScope } from "../features/overall/ReleaseScope";
import { useProjectOptions } from "../features/project/useProjectOptions";

const view = <K extends string, P>(load: () => Promise<Record<K, ComponentType<P>>>, name: K) =>
  lazy(() => load().then((module) => ({ default: module[name] })));

const PortfolioDate = view(() => import("./hub/PortfolioDate"), "PortfolioDate");
const PortfolioWarnings = view(() => import("./hub/PortfolioWarnings"), "PortfolioWarnings");
const PortfolioScope = view(() => import("./hub/PortfolioScope"), "PortfolioScope");
const PortfolioReports = view(() => import("./hub/PortfolioReports"), "PortfolioReports");
const ProjectDate = view(() => import("./hub/ProjectDate"), "ProjectDate");
const ProjectWarnings = view(() => import("./hub/ProjectWarnings"), "ProjectWarnings");
const ProjectScope = view(() => import("./hub/ProjectScope"), "ProjectScope");
const ProjectReports = view(() => import("./hub/ProjectReports"), "ProjectReports");
const PodDate = view(() => import("./hub/PodDate"), "PodDate");
const PodWarnings = view(() => import("./hub/PodWarnings"), "PodWarnings");
const PodScope = view(() => import("./hub/PodScope"), "PodScope");
const PodReports = view(() => import("./hub/PodReports"), "PodReports");

/*
 * The Projects hub: one route component for its three levels.
 *
 *   /portfolio/:tab                          All projects
 *   /projects/:projectId/:tab                one project
 *   /projects/:projectId/pods/:podId/:tab    one pod, its part of that project
 *
 * The tabs are date, warnings, scope and reports. The level is chosen with the
 * switcher (the title) and the pod bar, the question with the tabs; the URL
 * holds both, and the URL always wins. A link without a tab, or with one the
 * role does not have there, is placed on the role's first by the guard
 * (app/access.ts), as is every address from before the hub.
 */
export function HubPage({ level }: { level: HubLevel }) {
  const { projectId = "", podId = "", tab: raw } = useParams();
  // Until the guard has placed the link on a tab, there is nothing to draw.
  if (!isHubTab(raw)) return null;
  const place: HubPlace =
    level === "all"
      ? { level: "all" }
      : level === "project"
        ? { level: "project", projectId }
        : { level: "pod", projectId, podId };
  return <Hub place={place} tab={raw} />;
}

function Hub({ place, tab }: { place: HubPlace; tab: HubTab }) {
  const projectId = place.level === "all" ? "" : place.projectId;
  const project = useHubProject(projectId);
  const day = useDayWords();
  if (place.level !== "all" && !project.name) {
    return (
      <PanelState isLoading={project.isLoading} error={null}>
        <NotListed what="project" day={day} />
      </PanelState>
    );
  }
  return place.level === "all" ? (
    <Frame place={place} tab={tab} />
  ) : (
    <ProjectFrame place={place} tab={tab} name={project.name ?? ""} />
  );
}

/** A project, or a pod of it: the recent list, the assistant's subject, the pod checks. */
function ProjectFrame({
  place,
  tab,
  name,
}: {
  place: Exclude<HubPlace, { level: "all" }>;
  tab: HubTab;
  name: string;
}) {
  const { access } = useRole();
  const { search } = useLocation();
  const day = useDayWords();
  const { opened } = useProjectOptions();
  const pod = useDirectoryPod(place.level === "pod" ? place.podId : "");
  const tree = useOwnTree();
  const projects = useProjects();
  const pods = usePodChoices(place.projectId, name);
  const treePod =
    place.level === "pod" ? tree.data?.pods.find((item) => item.id === place.podId) : undefined;
  const podName = pod?.name ?? treePod?.name;
  useAssistantSubject(
    place.level === "pod"
      ? podName
        ? { kind: "pod", name: podName }
        : null
      : { kind: "project", name },
  );
  useEffect(() => {
    opened(place.projectId);
  }, [opened, place.projectId]);

  if (place.level === "pod") {
    const onProjects = pod?.project_ids ?? treePod?.parent_ids;
    if (!podName && !pods.isLoading) return <NotListed what="pod" day={day} />;
    if (onProjects && !onProjects.includes(place.projectId)) {
      return (
        <NotOnProject
          podId={place.podId}
          podName={podName ?? "This pod"}
          projectName={name}
          projectIds={onProjects}
          projectNames={(id) =>
            projects.data?.find((item) => item.id === id)?.name ??
            (tree.data ? ownProjects(tree.data).find((item) => item.id === id)?.name : undefined)
          }
          tab={tab}
        />
      );
    }
    // A counts-only reader opens only the pods the bulk read gives them (five people or more).
    // Nothing of the pod is drawn or read until that is known: its reads are refused otherwise.
    if (access.hub.countsOnly && pods.isLoading) {
      return <p className="text-grey-secondary">Loading…</p>;
    }
    if (access.hub.countsOnly && !pods.choices.some((choice) => choice.id === place.podId)) {
      return (
        <Navigate
          to={hubHref(
            access.hub.tabs,
            { place, tab },
            { level: "project", projectId: place.projectId },
            tab,
            search,
          )}
          replace
        />
      );
    }
  }
  return <Frame place={place} tab={tab} pods={pods.choices} />;
}

/**
 * The frame every level shares: the header, the tab bar with its badges, the
 * pod bar (within a project), what the project's tabs cover, a past day said
 * once, then the tab's own view.
 */
function Frame({
  place,
  tab,
  pods = [],
}: {
  place: HubPlace;
  tab: HubTab;
  pods?: ReturnType<typeof usePodChoices>["choices"];
}) {
  const { access } = useRole();
  const { asOf, label } = useViewingDate();
  const projectId = place.level === "all" ? "" : place.projectId;
  const release = useReleaseChoice(projectId);
  const readiness = useReadsReadiness(projectId);
  const badges = useHubBadges(place);
  const releaseId = release.releaseId;
  const atProject = place.level === "project";
  // A pod's tabs say a past day once too, at their top; its Reports has no change to turn off.
  const atPod = place.level === "pod" && tab !== "reports";
  return (
    // A past day is said once, in the paragraph below: no section says it again.
    <PastDaySaidContext.Provider value={asOf !== null && (atProject || atPod)}>
      <div className="op-viz">
        <HubHeader place={place} tab={tab} releaseId={releaseId} />
        <HubTabs place={place} tab={tab} badges={badges} />
        {place.level !== "all" ? <PodBar place={place} tab={tab} pods={pods} /> : null}
        {atProject && tab !== "reports" ? (
          <>
            <ReleaseScope projectId={projectId} releaseId={releaseId} onChange={release.onChange} />
            {release.gone ? (
              <p
                className={`${release.readable ? "-mt-5" : ""} mb-8 text-[13px] text-grey-secondary`}
              >
                The release in the link no longer exists, so this shows the whole project.
              </p>
            ) : null}
          </>
        ) : null}
        {asOf && atProject && tab !== "reports" ? (
          <p className="mb-8 rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
            {pastDayWords(access.overall, label ?? "")} Gate sign-offs and questions are not kept
            per day: they show how they stand now, for the requirements of that day.
            {readiness ? " Release readiness shows how it stands now." : ""} Go back to today to
            make changes.
          </p>
        ) : null}
        {asOf && atPod ? (
          <p className="mb-8 rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
            You&apos;re viewing {label}.
            {tab === "scope"
              ? " Gate sign-offs are not kept per day: they show how they stand now, for the requirements of that day."
              : ""}{" "}
            Go back to today to make changes.
          </p>
        ) : null}
        <Suspense fallback={<p className="text-grey-secondary">Loading…</p>}>
          <View key={placePath(place)} place={place} tab={tab} releaseId={releaseId} />
        </Suspense>
      </div>
    </PastDaySaidContext.Provider>
  );
}

function View({
  place,
  tab,
  releaseId,
}: {
  place: HubPlace;
  tab: HubTab;
  releaseId: string;
}): ReactNode {
  if (place.level === "all") {
    if (tab === "date") return <PortfolioDate />;
    if (tab === "warnings") return <PortfolioWarnings />;
    if (tab === "scope") return <PortfolioScope />;
    return <PortfolioReports />;
  }
  const { projectId } = place;
  if (place.level === "project") {
    if (tab === "date") return <ProjectDate projectId={projectId} releaseId={releaseId} />;
    if (tab === "warnings") return <ProjectWarnings projectId={projectId} releaseId={releaseId} />;
    if (tab === "scope") return <ProjectScope projectId={projectId} releaseId={releaseId} />;
    return <ProjectReports projectId={projectId} />;
  }
  const { podId } = place;
  if (tab === "date") return <PodDate projectId={projectId} podId={podId} />;
  if (tab === "warnings") return <PodWarnings projectId={projectId} podId={podId} />;
  if (tab === "scope") return <PodScope projectId={projectId} releaseId={releaseId} />;
  return <PodReports projectId={projectId} podId={podId} />;
}

/** What a past day shows as it stood, named by the sections this role has. */
function pastDayWords(
  overall: { forecast: boolean; timeline: boolean; requirements: boolean; risks: boolean },
  label: string,
): string {
  const kept = [
    overall.forecast ? "the forecast" : overall.timeline ? "the pods' dates" : null,
    overall.requirements ? "requirements" : null,
    overall.risks ? "warnings" : null,
  ].filter((part): part is string => part !== null);
  if (kept.length === 0) return "";
  const joined =
    kept.length === 1 ? kept[0] : `${kept.slice(0, -1).join(", ")} and ${kept[kept.length - 1]}`;
  const one = kept.length === 1 && kept[0] === "the forecast";
  return `${joined.charAt(0).toUpperCase()}${joined.slice(1)} ${one ? "is as it" : "are as they"} stood on ${label}.`;
}

function NotListed({ what, day }: { what: "project" | "pod"; day: string }) {
  return (
    <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
      That {what} is not in the directory{day === "today" ? "" : ` ${day}`}.{" "}
      <Link to="/projects" className="font-bold">
        Open Projects
      </Link>
    </p>
  );
}

/** A pod opened on a project it does not work on: the projects it does work on. */
function NotOnProject({
  podId,
  podName,
  projectName,
  projectIds,
  projectNames,
  tab,
}: {
  podId: string;
  podName: string;
  projectName: string;
  projectIds: string[];
  projectNames: (id: string) => string | undefined;
  tab: HubTab;
}) {
  const { access } = useRole();
  const { search } = useLocation();
  const listed = projectIds
    .map((id) => ({ id, name: projectNames(id) }))
    .filter((item): item is { id: string; name: string } => Boolean(item.name));
  return (
    <div className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
      <p>
        {podName} doesn&apos;t work on {projectName}.{listed.length > 0 ? " It works on:" : ""}
      </p>
      {listed.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-2">
          {listed.map((project) => (
            <li key={project.id}>
              <Link
                to={hubHref(
                  access.hub.tabs,
                  { place: { level: "all" }, tab },
                  { level: "pod", projectId: project.id, podId },
                  tab,
                  search,
                )}
                className="inline-flex min-h-9 items-center rounded-full border border-grey-border bg-white px-3 text-[13px] font-bold text-ink no-underline hover:bg-grey-fill"
              >
                {project.name}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * `/projects`: where Projects opens for the role. Every reader of project
 * lists lands on All projects' date; a developer on their own pod's date, on
 * its project. The link's query and hash go along.
 */
export function ProjectsLanding() {
  const { access } = useRole();
  const location = useLocation();
  const day = useDayWords();
  const lists = access.hub.tabs.all.includes("date");
  const tree = useOwnTree(!lists);
  if (lists) {
    return (
      <Navigate
        to={{
          pathname: placePath({ level: "all" }, "date"),
          search: location.search,
          hash: location.hash,
        }}
        replace
      />
    );
  }
  const own = tree.data ? ownProjects(tree.data) : [];
  const withPod = own.find((project) => project.pods.some((pod) => pod.own && pod.opens));
  const pod = withPod?.pods.find((item) => item.own && item.opens);
  const target =
    withPod && pod
      ? placePath({ level: "pod", projectId: withPod.id, podId: pod.id }, "date")
      : own[0]
        ? placePath({ level: "project", projectId: own[0].id })
        : null;
  if (target) {
    return (
      <Navigate to={{ pathname: target, search: location.search, hash: location.hash }} replace />
    );
  }
  return (
    <PanelState
      isLoading={tree.isLoading}
      error={tree.error}
      isEmpty
      emptyText={
        day === "today"
          ? "No project is yours to open yet."
          : `No project was yours to open ${day}.`
      }
    >
      {null}
    </PanelState>
  );
}
