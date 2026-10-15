import { Link } from "react-router-dom";

import { useRole } from "../../app/role";

/**
 * "1 no data (no Jira link)": linked to Admin › Directory, where a missing Jira
 * link or chat id is put right, for someone who can open Admin; plain words
 * for everyone else, with no line saying who could.
 */
export function CoverageNoData({
  words,
  fixedInDirectory,
}: {
  words: string;
  fixedInDirectory: boolean;
}) {
  const { access } = useRole();
  if (!words) return null;
  return fixedInDirectory && access.pages.admin ? (
    <Link to="/admin?tab=directory" className="font-bold text-magenta">
      {words}
    </Link>
  ) : (
    <>{words}</>
  );
}
