import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useOwnTree, usePrograms, useProjects } from "../../app/directory";
import { programPath } from "../../app/links";
import { useRole } from "../../app/role";
import { useDayWords } from "../../app/viewingDate";
import { verdictChip } from "../../components/ui/dateStripWords";
import { RagChip } from "../../components/ui/RagChip";
import { PERSON_KEY_WORDS } from "../../lib/words";
import { readState } from "../../lib/readState";
import { reasonLine, setBy } from "../delivery/factors";
import { ownProjects } from "../delivery/ownTree";
import { useDelivery } from "../overall/queries";
import { verdictPill } from "../portfolio/portfolioWords";
import { podLine, scrumMasterOf } from "../pods/podWords";
import { ProjectSwitcher } from "../project/ProjectSwitcher";
import { Breadcrumb } from "./Breadcrumb";
import { ALL_PROJECTS, type HubPlace, type HubTab } from "./hubPaths";
import { hubHref, podVerdict } from "./hubTabs";
import {
  useDirectoryPod,
  useHubProject,
  usePodAccess,
  usePodChoices,
  usePortfolioRow,
} from "./useHub";

/**
 * The hub's header, at every level: where the level sits (the trail), the
 * title, which is the switcher, the date verdict as its one pill, and the
 * facts in words on one or two lines. Today's health is said in words ("Today:
 * CHK-102 blocked"), never as a second colour. A counts-only reader gets no
 * names in it.
 */
export function HubHeader({
  place,
  tab,
  releaseId = "",
}: {
  place: HubPlace;
  tab: HubTab;
  releaseId?: string;
}) {
  if (place.level === "all") return <AllHeader tab={tab} />;
  if (place.level === "project") {
    return <ProjectHeader projectId={place.projectId} tab={tab} releaseId={releaseId} />;
  }
  return <PodHeader projectId={place.projectId} podId={place.podId} tab={tab} />;
}

function AllHeader({ tab }: { tab: HubTab }) {
  const { access } = useRole();
  return (
    <header className="mb-5 grid gap-1.5">
      <ProjectSwitcher place={{ level: "all" }} tab={tab} title={ALL_PROJECTS} />
      <p className="text-[13.5px] text-grey-body">
        {access.hub.allScope === "own"
          ? "The projects your pods work on. Pick one, or one of its pods, above."
          : "Every project you read. Pick one, or one of its pods, above."}
      </p>
    </header>
  );
}

/** The trail to a project: All projects (where the role has it), then its programme. */
function useTrail(projectId: string, tab: HubTab) {
  const { access } = useRole();
  const { search } = useLocation();
  const projects = useProjects();
  const programs = usePrograms();
  const tree = useOwnTree();
  const project = projects.data?.find((item) => item.id === projectId);
  const own = tree.data ? ownProjects(tree.data).find((item) => item.id === projectId) : undefined;
  const programId = project?.program_ids[0] ?? own?.programIds[0];
  const programme =
    programs.data?.find((program) => program.id === programId)?.name ??
    tree.data?.programs.find((program) => program.id === programId)?.name ??
    null;
  const readsAll = access.hub.tabs.all.length > 0;
  const readsProgrammes = access.hub.tabs.all.includes("date") && access.hub.allScope === "every";
  const steps: { label: string; to: string | null }[] = [];
  if (readsAll) {
    steps.push({
      label: ALL_PROJECTS,
      to: hubHref(
        access.hub.tabs,
        { place: { level: "project", projectId }, tab },
        { level: "all" },
        tab,
        search,
      ),
    });
  }
  if (programme) {
    steps.push({
      label: programme,
      to: readsProgrammes && programId ? programPath(programId) : null,
    });
  }
  return steps;
}

function ProjectHeader({
  projectId,
  tab,
  releaseId,
}: {
  projectId: string;
  tab: HubTab;
  releaseId: string;
}) {
  const { access, canReadProjectProgress } = useRole();
  const { project, own, name } = useHubProject(projectId);
  const steps = useTrail(projectId, tab);
  const { row } = usePortfolioRow(projectId, name ?? undefined);
  const forecast = access.overall.forecast;
  const delivery = useDelivery(projectId);
  const scope = releaseId
    ? delivery.query.data?.releases.find((item) => item.scope_id === releaseId)
    : delivery.query.data?.project;
  const pill = forecast && scope ? verdictChip(scope) : row ? verdictPill(row) : null;
  const countsOnly = access.hub.countsOnly;
  const readable = canReadProjectProgress || Boolean(own?.full);
  // Today's health, said in words: the project's progress read, which the date tab shares.
  const progress = useQuery({
    queryKey: ["project", projectId, "progress"],
    queryFn: () => apiClient.projectProgress(projectId),
    enabled: readable && !countsOnly,
  });
  const health = progress.data
    ? (setBy(progress.data.rag, progress.data.factors)?.text ?? null)
    : null;
  const jira = project?.metadata?.jira_project_key;
  const facts = [
    typeof jira === "string" && jira.trim() ? `Jira ${jira.trim()}` : null,
    ...(countsOnly
      ? []
      : (project?.people ?? [])
          .filter((person) => person.name)
          .map((person) => `${PERSON_KEY_WORDS[person.key] ?? person.key} ${person.name}`)),
  ].filter((part): part is string => part !== null);

  return (
    <header className="mb-5 grid gap-1.5">
      <Breadcrumb steps={steps} />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <ProjectSwitcher
          place={{ level: "project", projectId }}
          tab={tab}
          title={name ?? "Project"}
        />
        {pill ? (
          <RagChip tone={pill.tone} dot className="h-[26px] px-3 text-[12.5px]">
            {pill.label}
          </RagChip>
        ) : null}
      </div>
      {health ? <p className="text-[13.5px] text-grey-body">Today: {health}</p> : null}
      {facts.length > 0 ? (
        <p className="text-[13px] text-grey-secondary">{facts.join(" · ")}</p>
      ) : null}
    </header>
  );
}

function PodHeader({ projectId, podId, tab }: { projectId: string; podId: string; tab: HubTab }) {
  const { access } = useRole();
  const { search } = useLocation();
  const day = useDayWords();
  const pod = useDirectoryPod(podId);
  const { name: projectName } = useHubProject(projectId);
  const projects = useProjects();
  const tree = useOwnTree();
  const { choices } = usePodChoices(projectId, projectName ?? undefined);
  const trail = useTrail(projectId, tab);
  const steps = projectName
    ? [
        ...trail,
        {
          label: projectName,
          to: hubHref(
            access.hub.tabs,
            { place: { level: "pod", projectId, podId }, tab },
            { level: "project", projectId },
            tab,
            search,
          ),
        },
      ]
    : trail;
  const treePod = tree.data?.pods.find((item) => item.id === podId);
  // The pod's detail (check-ins, blockers, why its colour): the readers of every pod's,
  // and the person's own pods by their tree.
  const { full } = usePodAccess(podId);
  const rollup = useQuery({
    queryKey: ["pod", podId, "rollup"],
    queryFn: () => apiClient.podRollup(podId),
    enabled: full,
  });
  const checkins = useQuery({
    queryKey: ["pod", podId, "checkins"],
    queryFn: () => apiClient.podCheckins(podId),
    enabled: full,
  });
  const blockers = useQuery({
    queryKey: ["pod", podId, "blockers"],
    queryFn: () => apiClient.podBlockers(podId),
    enabled: full,
  });
  const choice = choices.find((item) => item.id === podId);
  const pill = choice ? podVerdict(choice) : null;
  const read = full ? readState(rollup) : undefined;
  const reason = rollup.data
    ? reasonLine(rollup.data.factors, rollup.data.source_names, rollup.data.rag)
    : null;
  const c = checkins.data;
  const counted = c ? c.confirmed + c.partial + c.stale + c.missing : null;
  const name = pod?.name ?? treePod?.name ?? "Pod";
  const others = (pod?.project_ids ?? treePod?.parent_ids ?? []).filter((id) => id !== projectId);
  const otherNames = others
    .map((id) => ({ id, name: projects.data?.find((item) => item.id === id)?.name }))
    .filter((item): item is { id: string; name: string } => Boolean(item.name));

  return (
    <header className="mb-5 grid gap-1.5">
      <Breadcrumb steps={steps} />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <ProjectSwitcher place={{ level: "pod", projectId, podId }} tab={tab} title={name} />
        {pill ? (
          <RagChip tone={pill.tone} dot className="h-[26px] px-3 text-[12.5px]">
            {pill.label}
          </RagChip>
        ) : null}
      </div>
      {full && !read?.error ? (
        <p className="text-[13.5px] text-grey-body">
          {read?.isLoading
            ? "Loading…"
            : `Today: ${
                reason ??
                (rollup.data?.rag && rollup.data.rag !== "unknown"
                  ? "no reason recorded."
                  : "nothing recorded yet.")
              }`}
        </p>
      ) : null}
      {pod && !access.hub.countsOnly ? (
        <p className="text-[13px] text-grey-body">
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
      ) : null}
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-grey-secondary">
        {otherNames.length > 0 ? (
          <span>
            Also on:{" "}
            {otherNames.map((other, index) => (
              <span key={other.id}>
                {index > 0 ? ", " : ""}
                <Link
                  to={hubHref(
                    access.hub.tabs,
                    { place: { level: "pod", projectId, podId }, tab },
                    { level: "pod", projectId: other.id, podId },
                    tab,
                    search,
                  )}
                  className="font-bold"
                >
                  {other.name}
                </Link>
              </span>
            ))}
          </span>
        ) : null}
        {access.pages.people ? (
          <Link to={`/people?pod=${encodeURIComponent(podId)}`} className="font-bold">
            People &amp; profiles →
          </Link>
        ) : null}
      </p>
    </header>
  );
}
