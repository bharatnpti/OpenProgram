import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../api/client";
import { ownLinks } from "../features/delivery/ownTree";
import {
  pageOf,
  redirectFor,
  redirectToast,
  roleOfferingPage,
  type DirectoryLinks,
} from "./access";
import { useOwnTree } from "./directory";
import { roleLabels, rolePriority, useRole } from "./role";

/**
 * A link to a page the viewing role is not offered opens the closest page it
 * has instead (app/access.ts `redirectFor`), with `replace`, so Back does not
 * bounce. Only when that is plain Today, with nothing of the role's own
 * standing in for the link, one toast says so; when the person holds another
 * role that is offered the page, the toast offers to switch to it. The page
 * itself never carries a line about roles.
 */
export function RouteGuard({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const role = useRole();
  const { access } = role;

  // Only a Delivery link to a pod or workstream needs the directory to say where to go,
  // and under the `own` scope any link to a node needs the person's part of the tree.
  const [, first, kind, id] = location.pathname.split("/");
  const delivery = pageOf(location.pathname) === "delivery" && first === "delivery";
  const own = access.deliveryScope === "own";
  const needsTree = delivery && own && Boolean(kind && id);
  const needsDirectory =
    delivery &&
    (kind === "pod" || kind === "workstream") &&
    (own || !(access.pages.delivery && access.delivery[kind]));
  // The same keys as app/directory.ts, so this shares its requests.
  const pods = useQuery({
    queryKey: ["directory", "pods"],
    queryFn: () => apiClient.pods(),
    enabled: needsDirectory,
  });
  const workstreams = useQuery({
    queryKey: ["directory", "workstreams"],
    queryFn: () => apiClient.workstreams(),
    enabled: needsDirectory,
  });
  const tree = useOwnTree(needsTree);
  const waiting =
    (needsDirectory && (pods.isPending || workstreams.isPending)) || (needsTree && tree.isPending);

  const redirect = useMemo(() => {
    if (waiting) return null;
    const links: DirectoryLinks = {
      podProjects: (podId) => pods.data?.find((pod) => pod.id === podId)?.project_ids ?? [],
      workstreamProjects: (wsId) =>
        workstreams.data?.find((item) => item.id === wsId)?.project_ids ?? [],
      // A tree that failed to load leaves the link to the page, which says so.
      own: tree.data ? ownLinks(tree.data) : null,
    };
    return redirectFor(location, access, role, links);
  }, [access, location, pods.data, role, tree.data, waiting, workstreams.data]);

  const target = redirect?.to ?? null;
  const missing = redirect?.missing ?? null;
  const asked = `${location.pathname}${location.search}${location.hash}`;
  useEffect(() => {
    if (target === null) return;
    if (missing) {
      // One role at a time is told to the API only under local dev sign-in; under a
      // real one every held role already counts, so there is nothing to switch to.
      const other = role.isDevMode
        ? roleOfferingPage(missing, role.roles, role.role, role.chatEnabled, rolePriority)
        : null;
      toast.info(redirectToast(missing, role.roleLabel), {
        // One toast however often the effect runs (StrictMode, a quick second link).
        id: "route-guard",
        action: other
          ? {
              label: `Switch to ${roleLabels[other]}`,
              onClick: () => {
                role.setRole(other);
                navigate(asked);
              },
            }
          : undefined,
      });
    }
    navigate(target, { replace: true });
    // The link asked for and where it goes decide this; the rest is read when it runs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, missing, asked]);

  if (waiting || redirect) {
    return <div className="text-grey-secondary">Loading…</div>;
  }
  return <>{children}</>;
}
