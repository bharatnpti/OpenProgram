import { useQueries } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { usePrograms } from "../../app/directory";
import { PanelState } from "../../components/PanelState";
import { Panel, Row } from "../../components/ui/Bits";
import { podTeam, whatEachRoleSees } from "./peopleWords";

/**
 * A pod's people as the team graph lists them, read only: each person by name
 * with their job title and their roles in the console. For a reader who reads
 * the team (a product owner, on their own projects' pods) but no pod's
 * check-ins or profiles. The trees are cached under the key the name index uses.
 */
export function PodTeam({ podId, podName }: { podId: string; podName: string }) {
  const programs = usePrograms();
  const trees = useQueries({
    queries: (programs.data ?? []).map((program) => ({
      queryKey: ["graph", "tree", program.id],
      queryFn: () => apiClient.programTree(program.id),
    })),
  });
  const loading = programs.isLoading || trees.some((tree) => tree.isLoading);
  const error = programs.error ?? trees.find((tree) => tree.error)?.error ?? null;
  const people = podTeam(
    trees.map((tree) => tree.data),
    podId,
  );
  return (
    <Panel title={`People in ${podName}`} note={people ? `${people.length} in the pod` : undefined}>
      <PanelState
        isLoading={loading}
        error={error}
        onRetry={() => trees.forEach((tree) => void tree.refetch())}
        isEmpty={!people || people.length === 0}
        emptyText="Nobody is in this pod yet."
      >
        <ul>
          {(people ?? []).map((person) => (
            <Row key={person.id} title={person.name} meta={person.line || undefined} />
          ))}
        </ul>
      </PanelState>
    </Panel>
  );
}

/**
 * What each role sees: one line per console role, and the pages its sidebar
 * offers, worded from the same access map the sidebar is built from. The
 * server still decides every read.
 */
export function WhatEachRoleSees() {
  const rows = whatEachRoleSees();
  return (
    <Panel title="What each role sees">
      <ul>
        {rows.map((row) => (
          <Row key={row.role} title={row.label} meta={row.sees}>
            <p className="mt-0.5 text-[12px] text-grey-body">Pages: {row.pages.join(" · ")}</p>
          </Row>
        ))}
      </ul>
    </Panel>
  );
}
