import { Link } from "react-router-dom";

import { portfolioPath } from "../../app/links";
import { useRole } from "../../app/role";
import { Sparkline } from "../../components/ui/Bits";
import { momentum } from "../../lib/momentum";
import { useHealthTrend } from "../hub/useHealthTrend";

/**
 * A programme's heading on All projects › Will we make the date?: its name,
 * its projects' verdicts in words, and its health over the last 30 days
 * ("Today's health, 30 days: steady, now red"), the one health line on this
 * tab, labelled as health so it never reads as the date. The line opens the
 * programme's Early warnings, where its health is said in full. The 30 days
 * are for the readers of programmes (manager, executive, admin).
 */
export function ProgrammeLine({
  id,
  name,
  line,
}: {
  id: string;
  name: string;
  line: string | null;
}) {
  const { access } = useRole();
  const shown = access.hub.portfolioHealth && id !== "";
  const trend = useHealthTrend("program", id, shown);
  const m = trend.data ? momentum(trend.data.points) : null;
  return (
    <div className="mb-3 mt-4 flex flex-wrap items-center gap-x-4 gap-y-1">
      <h3 className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 text-[15px] font-extrabold">
        {name}
        {line ? <span className="text-[12px] font-normal text-grey-secondary">{line}</span> : null}
      </h3>
      {shown ? (
        <Link
          to={`${portfolioPath("warnings")}?program=${encodeURIComponent(id)}`}
          className="flex min-h-11 min-w-0 items-center gap-2 text-[12px] text-grey-secondary no-underline hover:text-ink sm:min-h-0"
        >
          {m && m.reportedDays >= 2 ? (
            <span className="block w-[96px] flex-none">
              <Sparkline values={m.values} label={`${name}'s health over 30 days`} />
            </span>
          ) : null}
          <span>
            <span className="font-bold text-grey-body">Today&apos;s health, 30 days:</span>{" "}
            {m ? m.label : trend.isError ? "could not be read" : "reading…"}
            <span aria-hidden> →</span>
          </span>
        </Link>
      ) : null}
    </div>
  );
}
