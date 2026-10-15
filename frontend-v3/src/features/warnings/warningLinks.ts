import type { WarningItemDto } from "../../api/schema";
import { podTabPath, projectTabPath } from "../../app/links";

/** Where an item's project, and its pod, open: their own early warnings. */
export type WarningLinks = {
  project: (item: WarningItemDto) => string;
  /** Left out: the pods are named, not linked. */
  pod?: (item: WarningItemDto, podId: string) => string;
};

export const WARNING_LINKS: WarningLinks = {
  project: (item) => projectTabPath(item.project_id, "warnings"),
  pod: (item, podId) => podTabPath(item.project_id, podId, "warnings"),
};
