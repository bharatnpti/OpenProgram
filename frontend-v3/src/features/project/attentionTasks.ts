// A project's tasks that need a decision, pure. Type imports, and runtime
// imports by their .ts path, so `node --test` runs it as written.
import type { ProjectProgressResponse, Rag } from "../../api/schema";
import { plural } from "../../lib/words.ts";

type Task = ProjectProgressResponse["tasks"][number];

/** The tasks "Needs attention" lists before "Show all". */
export const ATTENTION_SHOWN = 8;

const SEVERITY: Record<Rag, number> = { red: 3, amber: 2, unknown: 1, green: 0 };

/**
 * The tasks that are not green, worst first: red, amber, then those with no
 * status (silence is no clean bill of health); among equals the nearest due
 * date first, then A to Z.
 */
export function attentionTasks(tasks: readonly Task[]): Task[] {
  return tasks
    .filter((task) => task.rag !== "green")
    .sort(
      (a, b) =>
        SEVERITY[b.rag] - SEVERITY[a.rag] ||
        (a.deadline ?? "9999").localeCompare(b.deadline ?? "9999") ||
        a.name.localeCompare(b.name),
    );
}

/** The panel's note: "worst 8 of 11 tasks", or "3 tasks". */
export function attentionNote(count: number, shown = ATTENTION_SHOWN): string {
  return count > shown ? `worst ${shown} of ${count} tasks` : plural(count, "task", "tasks");
}
