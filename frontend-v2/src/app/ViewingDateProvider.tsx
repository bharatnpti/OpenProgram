import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useNavigationType } from "react-router-dom";

import { setReadOnlyReason } from "../api/client";
import { todayIso } from "../lib/today";
import {
  TODAY_CHOSEN_STATE,
  VIEWING_DATE_PARAM,
  choseToday,
  formatDayLabel,
  parseViewingDate,
  resolveViewingDate,
  viewingDateApplies,
  withViewingDateParam,
} from "../lib/viewingDate";
import { ViewingDateContext, readOnlyReason, type ViewingDateValue } from "./viewingDate";

/**
 * Holds the console's one viewing date and keeps the URL saying what it is.
 *
 * Must sit inside the router. The date is read from `?asOf=`. The last past day
 * shown is remembered so an in-app link, which drops the param, lands on that
 * same day, and the param is then written back so the URL never disagrees with
 * the screen.
 */
export function ViewingDateProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigation = useNavigationType();
  const navigate = useNavigate();
  const today = useTodayIso();
  const [carried, setCarried] = useState<string | null>(null);

  const raw = new URLSearchParams(location.search).get(VIEWING_DATE_PARAM);
  const applies = viewingDateApplies(location.pathname);
  const { past, param } = resolveViewingDate({
    raw,
    carried,
    navigation,
    choseToday: choseToday(location.state),
    today,
    applies,
  });
  const asOf = past ?? today;
  const label = formatDayLabel(asOf, today);

  // Installed on the shared client during render, like the acting-as identity,
  // so no change a newly shown screen sends can slip out before it.
  setReadOnlyReason(past ? readOnlyReason(label) : null);

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
      asOf,
      today,
      isPast: past !== null,
      label,
      applies,
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
    [applies, asOf, label, location, navigate, past, today],
  );

  return <ViewingDateContext.Provider value={value}>{children}</ViewingDateContext.Provider>;
}

/**
 * Today, kept current across midnight.
 *
 * Screens used to read the clock on every render; with the date held here, a
 * console left open overnight would otherwise keep asking for yesterday, and
 * allow changes to it, until the next navigation. Timers in a hidden tab can
 * run late, so coming back to the tab checks too.
 */
function useTodayIso(): string {
  const [today, setToday] = useState(todayIso);
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

function markTodayChosen(state: unknown): Record<string, unknown> {
  const current = typeof state === "object" && state !== null ? state : {};
  return { ...current, [TODAY_CHOSEN_STATE]: true };
}
