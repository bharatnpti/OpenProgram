import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useNavigationType } from "react-router-dom";

import { setViewingAsOf } from "../api/asOf";
import { setReadOnlyReason } from "../api/client";
import {
  VIEWING_DATE_PARAM,
  choseToday,
  formatDayLabel,
  markTodayChosen,
  parseViewingDate,
  readOnlyReason,
  resolveViewingDate,
  showsCurrentState,
  todayIso,
  withViewingDateParam,
} from "../lib/viewingDate";
import { ViewingDateContext, type ViewingDateValue } from "./viewingDate";

/**
 * Holds the console's one viewing date and keeps the URL saying what it is.
 * Ported from frontend-v2's ViewingDateProvider; keep the rules in step.
 *
 * Must sit inside the router. The date is read from `?asOf=`. The last past day
 * shown is remembered so an in-app link, which drops the param, lands on that
 * same day, and the param is then written back so the URL never disagrees with
 * the screen. Admin keeps the param but reads and changes the current state.
 */
export function ViewingDateProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigation = useNavigationType();
  const navigate = useNavigate();
  const today = useTodayIso();
  const [carried, setCarried] = useState<string | null>(null);

  const raw = new URLSearchParams(location.search).get(VIEWING_DATE_PARAM);
  const { past, param } = resolveViewingDate({
    raw,
    carried,
    navigation,
    choseToday: choseToday(location.state),
    today,
  });
  const asOf = showsCurrentState(location.pathname) ? null : past;
  const label = past ? formatDayLabel(past, today) : null;
  const reason = asOf && label ? readOnlyReason(label) : null;

  // Installed on the shared client during render, like the acting-as identity,
  // so no read or change a newly shown screen sends can slip out before it.
  setViewingAsOf(asOf);
  setReadOnlyReason(reason);

  // Only what a committed screen showed is carried. The router renders
  // navigations as transitions, so setting this from a click could be undone by
  // a render of the old location in between.
  useEffect(() => {
    setCarried(past);
  }, [past]);

  // A carried day goes back into the URL, and a value that names no past day
  // (garbage, today, a future day) comes out of it.
  useEffect(() => {
    if (param === raw) return;
    navigate(
      {
        pathname: location.pathname,
        search: withViewingDateParam(location.search, param),
        hash: location.hash,
      },
      { replace: true, state: location.state },
    );
  }, [param, raw, location, navigate]);

  const value = useMemo<ViewingDateValue>(
    () => ({
      chosen: past,
      asOf,
      today,
      label,
      readOnly: reason !== null,
      reason,
      setViewingDate: (next) => {
        const chosen = next === null ? null : parseViewingDate(next, today);
        // Empty, malformed and future values leave the date where it is.
        if (next !== null && next !== today && chosen === null) return;
        navigate(
          {
            pathname: location.pathname,
            search: withViewingDateParam(location.search, chosen),
            hash: location.hash,
          },
          {
            replace: true,
            state: chosen === null ? markTodayChosen(location.state) : location.state,
          },
        );
      },
    }),
    [asOf, label, location, navigate, past, reason, today],
  );

  return <ViewingDateContext.Provider value={value}>{children}</ViewingDateContext.Provider>;
}

/**
 * Today, kept current across midnight, so a console left open overnight does
 * not keep asking for yesterday (and refusing changes to it). Timers in a
 * hidden tab can run late, so coming back to the tab checks too.
 */
function useTodayIso(): string {
  const [today, setToday] = useState(() => todayIso());
  useEffect(() => {
    const check = () => setToday(todayIso());
    const now = new Date();
    const nextDay = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
    const timer = window.setTimeout(check, nextDay - now.getTime() + 1000);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, [today]);
  return today;
}
