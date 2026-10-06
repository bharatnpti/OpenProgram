// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.

/**
 * The portfolio heat rows Today draws.
 *
 * Projects and pods always get their row. Workstreams are optional, and the
 * directory lists only the ones holding a task or work item, so the
 * workstreams row is drawn only when one is in use: a tenant that runs
 * everything through pods sees no "Workstreams" label beside no tiles, and
 * never a row of empty unknown ones.
 */
export function shownHeatRows<T extends { kind: string; total: number }>(rows: T[]): T[] {
  return rows.filter((row) => row.kind !== "workstream" || row.total > 0);
}
