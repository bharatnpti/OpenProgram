import type { PortfolioScopeResponse, ScopeFindingDto, ScopeProjectDto } from "../../api/schema";
import { td, th } from "../../components/PanelState";
import { formatDay } from "../../lib/format";
import { cn } from "../../lib/utils";
import { VizPanel } from "../portfolio/VizPanel";
import { addedWords, foundWords, labelHeading, ownerWords, withoutLabelWords } from "./scopeWords";

/**
 * Each project against its planning baseline: planned, added after, without the
 * baseline's own change label, removed, and its open Jira hygiene findings.
 * A project opens its own baseline and hygiene below (`?project=`). A table on a
 * wide screen, a card per project on a phone: nothing scrolls sideways.
 */
export function BaselineTable({
  scope,
  picked,
  onPick,
}: {
  scope: PortfolioScopeResponse;
  picked: string | null;
  onPick: (projectId: string) => void;
}) {
  const plans = scope.reads_baselines;
  const label = labelHeading(scope.projects);
  return (
    <VizPanel
      title={plans ? "Against the plan" : "Your projects"}
      titleId="scope-plan"
      note={plans ? "keys and counts, never who added them" : undefined}
    >
      <div className="overflow-x-auto rounded-2xl border border-grey-border max-sm:hidden">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th scope="col" className={th}>
                Project
              </th>
              {plans ? (
                <>
                  <th scope="col" className={th}>
                    Planning complete
                  </th>
                  <th scope="col" className={cn(th, "text-right")}>
                    Planned
                  </th>
                  <th scope="col" className={cn(th, "text-right")}>
                    Added after
                  </th>
                  <th scope="col" className={cn(th, "text-right")}>
                    {label}
                  </th>
                  <th scope="col" className={cn(th, "text-right")}>
                    Removed
                  </th>
                </>
              ) : null}
              {scope.hygiene_enabled ? (
                <th scope="col" className={cn(th, "text-right")}>
                  Hygiene open
                </th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {scope.projects.map((project) => (
              <tr key={project.id} className={project.id === picked ? "bg-grey-fill" : undefined}>
                <td className={td}>
                  <ProjectButton project={project} picked={project.id === picked} onPick={onPick} />
                  {plans && project.added_keys.length ? (
                    <span className="mt-0.5 block text-[12px] text-grey-secondary">
                      Added: {project.added_keys.join(", ")}
                    </span>
                  ) : null}
                </td>
                {plans ? (
                  <>
                    <td className={td}>
                      {project.plan ? formatDay(project.plan.planned_on) : "Not marked yet"}
                    </td>
                    <td className={cn(td, "text-right tabular-nums")}>
                      {project.plan ? project.plan.planned : "—"}
                    </td>
                    <td className={cn(td, "text-right font-bold tabular-nums")}>
                      {project.plan ? addedWords(project) : "—"}
                    </td>
                    <td className={cn(td, "text-right tabular-nums")}>
                      {withoutLabelWords(project)}
                    </td>
                    <td className={cn(td, "text-right tabular-nums")}>
                      {project.plan ? project.plan.removed : "—"}
                    </td>
                  </>
                ) : null}
                {scope.hygiene_enabled ? (
                  <td className={cn(td, "text-right tabular-nums")}>{project.hygiene_open}</td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="grid gap-2 sm:hidden">
        {scope.projects.map((project) => (
          <li
            key={project.id}
            className={cn(
              "rounded-2xl border border-grey-border p-3",
              project.id === picked && "bg-grey-fill",
            )}
          >
            <ProjectButton project={project} picked={project.id === picked} onPick={onPick} />
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[13px]">
              {plans ? (
                project.plan ? (
                  <>
                    <Fact label="Planning complete" value={formatDay(project.plan.planned_on)} />
                    <Fact label="Planned" value={String(project.plan.planned)} />
                    <Fact label="Added after" value={addedWords(project)} />
                    <Fact label={label} value={withoutLabelWords(project)} />
                    <Fact label="Removed" value={String(project.plan.removed)} />
                  </>
                ) : (
                  <Fact label="Planning complete" value="Not marked yet" />
                )
              ) : null}
              {scope.hygiene_enabled ? (
                <Fact label="Hygiene open" value={String(project.hygiene_open)} />
              ) : null}
            </dl>
            {plans && project.added_keys.length ? (
              <p className="mt-1.5 text-[12px] text-grey-secondary break-words">
                Added: {project.added_keys.join(", ")}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </VizPanel>
  );
}

function ProjectButton({
  project,
  picked,
  onPick,
}: {
  project: ScopeProjectDto;
  picked: boolean;
  onPick: (projectId: string) => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={picked}
      onClick={() => onPick(project.id)}
      className="text-left font-bold text-ink underline-offset-2 hover:underline"
    >
      {project.name}
    </button>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
        {label}
      </dt>
      <dd className="font-bold text-ink tabular-nums">{value}</dd>
    </div>
  );
}

/**
 * The open Jira hygiene findings across the projects: the issue, the rule, who
 * owns it (or their team, for a reader who sees no person) and since when. A
 * finding is never sent from here: a rule an admin switched to ask asks its
 * owner in the day report, and the row says so. An executive reads each rule's
 * count.
 */
export function HygieneTable({ scope }: { scope: PortfolioScopeResponse }) {
  if (!scope.hygiene_enabled) {
    return (
      <VizPanel title="Jira hygiene" titleId="scope-hygiene">
        <p className="text-[14px] text-grey-body">The Jira hygiene scan is off.</p>
      </VizPanel>
    );
  }
  const open = scope.findings_total;
  if (scope.counts_only) {
    return (
      <VizPanel title="Jira hygiene" titleId="scope-hygiene" note={`${open} open`}>
        <ul>
          {scope.rules.map((rule) => (
            <li
              key={rule.id}
              className="flex items-baseline justify-between gap-3 border-t border-grey-border py-2.5 text-[14px] first:border-t-0"
            >
              <span className="min-w-0">{rule.name}</span>
              <span className="flex-none font-bold tabular-nums">{rule.open}</span>
            </li>
          ))}
        </ul>
      </VizPanel>
    );
  }
  return (
    <VizPanel
      title="Jira hygiene"
      titleId="scope-hygiene"
      note={
        open > scope.findings.length
          ? `${scope.findings.length} of ${open} open, oldest first`
          : `${open} open, oldest first`
      }
    >
      {scope.findings.length === 0 ? (
        <p className="text-[14px] text-grey-body">No open findings. Jira is tidy.</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-2xl border border-grey-border max-sm:hidden">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th scope="col" className={th}>
                    Ticket
                  </th>
                  <th scope="col" className={th}>
                    Rule
                  </th>
                  <th scope="col" className={th}>
                    Project
                  </th>
                  <th scope="col" className={th}>
                    Owner
                  </th>
                  <th scope="col" className={th}>
                    Found
                  </th>
                </tr>
              </thead>
              <tbody>
                {scope.findings.map((finding) => (
                  <tr key={finding.id}>
                    <td className={td}>
                      <Ticket finding={finding} />
                    </td>
                    <td className={td}>{finding.rule_name}</td>
                    <td className={td}>{finding.project_name}</td>
                    <td className={td}>
                      <Owner finding={finding} />
                    </td>
                    <td className={cn(td, "whitespace-nowrap tabular-nums")}>
                      <Found finding={finding} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="sm:hidden">
            {scope.findings.map((finding) => (
              <li
                key={finding.id}
                className="grid gap-1 border-t border-grey-border py-3 text-[13px] first:border-t-0"
              >
                <Ticket finding={finding} />
                <span>
                  {finding.rule_name} · {finding.project_name}
                </span>
                <Owner finding={finding} />
                <span className="text-grey-secondary tabular-nums">
                  Found <Found finding={finding} />
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </VizPanel>
  );
}

function Ticket({ finding }: { finding: ScopeFindingDto }) {
  return (
    <span>
      <span className="font-bold text-ink">{finding.issue_key}</span>
      {finding.title ? (
        <span className="mt-0.5 block text-[12px] text-grey-secondary">{finding.title}</span>
      ) : null}
    </span>
  );
}

function Owner({ finding }: { finding: ScopeFindingDto }) {
  return (
    <span>
      {ownerWords(finding)}
      {finding.asks ? (
        <span className="mt-0.5 block text-[12px] text-grey-secondary">
          Asked in the day report
        </span>
      ) : null}
    </span>
  );
}

function Found({ finding }: { finding: ScopeFindingDto }) {
  return (
    <>
      {foundWords(finding)}
      {finding.held ? (
        <span className="mt-0.5 block text-[12px] text-grey-secondary">Held: Jira was behind</span>
      ) : null}
    </>
  );
}
