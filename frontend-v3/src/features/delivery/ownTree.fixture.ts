// Trees as `GET /me/delivery-tree` answers them on the demo seed, for the unit
// tests of the navigator, the palette and the redirects. Made up, demo-shaped.
import type { DeliveryTreeNodeResponse, DeliveryTreeResponse } from "../../api/schema";

type Node = Omit<DeliveryTreeNodeResponse, "kind">;

const node = (
  kind: DeliveryTreeNodeResponse["kind"],
  id: string,
  name: string,
  access: DeliveryTreeNodeResponse["access"],
  extra: Partial<Node> = {},
): DeliveryTreeNodeResponse => ({
  id,
  kind,
  name,
  access,
  rag: access === "name" ? null : "unknown",
  own: true,
  parent_ids: [],
  ...extra,
});

const program = node("program", "program-digital", "Digital Platform Program", "name");
const checkout = (access: "panel" | "name", rag: DeliveryTreeNodeResponse["rag"] = null) =>
  node("project", "project-checkout", "Checkout Revamp", access, {
    rag,
    parent_ids: ["program-digital"],
  });
const inCheckout = { parent_ids: ["project-checkout"] };

/** Kai, a developer in the Payments Pod: his project by name, his pod's data only. */
export const developerTree: DeliveryTreeResponse = {
  as_of: "2026-10-06",
  programs: [program],
  projects: [checkout("name")],
  pods: [
    node("pod", "pod-payments", "Payments Pod", "panel", { rag: "red", ...inCheckout }),
    node("pod", "pod-storefront", "Storefront Pod", "name", { own: false, ...inCheckout }),
  ],
};

/** Ira, a scrum master running Payments and Identity: both projects, every pod under them. */
export const scrumMasterTree: DeliveryTreeResponse = {
  as_of: "2026-10-06",
  programs: [program],
  projects: [
    checkout("panel", "red"),
    node("project", "project-identity", "Identity Platform", "panel", {
      rag: "amber",
      parent_ids: ["program-digital"],
    }),
  ],
  pods: [
    node("pod", "pod-identity", "Identity Pod", "panel", {
      rag: "amber",
      parent_ids: ["project-identity"],
    }),
    node("pod", "pod-payments", "Payments Pod", "panel", { rag: "red", ...inCheckout }),
    node("pod", "pod-storefront", "Storefront Pod", "dates", {
      rag: "green",
      own: false,
      ...inCheckout,
    }),
  ],
};

/** Mina, a product owner in the Payments Pod: her project, its pods' dates and colours. */
export const productOwnerTree: DeliveryTreeResponse = {
  as_of: "2026-10-06",
  programs: [program],
  projects: [checkout("panel", "red")],
  pods: [
    node("pod", "pod-payments", "Payments Pod", "dates", { rag: "red", ...inCheckout }),
    node("pod", "pod-storefront", "Storefront Pod", "dates", {
      rag: "green",
      own: false,
      ...inCheckout,
    }),
  ],
};
