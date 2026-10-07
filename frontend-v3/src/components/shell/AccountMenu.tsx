import { useEffect, useRef, useState } from "react";

import type { ApiError } from "../../api/client";
import { initialsFor, useRole } from "../../app/role";
import { accountLine, rolesLabel } from "../../app/roleWords";
import { CheckinScheduleDialog } from "../../features/checkin/CheckinScheduleDialog";
import { noCheckinWords, scheduleSummary } from "../../features/checkin/schedule";
import { isNoCheckin, useMyCheckinPreference } from "../../features/checkin/useMyCheckinPreference";
import { cn } from "../../lib/utils";
import { ActingAsControls } from "./ActingAs";
import { ViewingDateControl } from "./ViewingDate";

/**
 * The avatar's menu: who you are, your own check-in schedule, and Sign out
 * under real sign-in. The schedule is here rather than on a Today screen
 * because anyone with a member record is asked to check in, whichever lens
 * they view through, and this menu is on every screen.
 */
export function AccountMenu() {
  const { displayName, roles, isDevMode, logout, people } = useRole();
  const [open, setOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const preference = useMyCheckinPreference(open);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const who = rolesLabel(roles);
  const summary = preference.data
    ? scheduleSummary(preference.data)
    : preference.isError
      ? isNoCheckin(preference.error)
        ? noCheckinWords(
            (preference.error as ApiError).status,
            (preference.error as ApiError).message,
          )
        : "Couldn't load your schedule."
      : "Loading…";
  const item =
    "flex w-full flex-col items-start rounded-xl px-3 py-2.5 text-left text-[14px] hover:bg-grey-fill";
  const menuLabel = "mb-1.5 text-[12px] font-bold uppercase tracking-wide text-grey-secondary";

  return (
    <div ref={container} className="relative">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls="account-menu"
        aria-label={`${displayName}, your account`}
        onClick={() => setOpen((current) => !current)}
        className="flex items-center gap-2 rounded-full text-left"
      >
        <span
          className={cn(
            "grid h-10 w-10 flex-none place-items-center rounded-full bg-ink text-[12px] font-extrabold text-white ring-2 ring-offset-2",
            open ? "ring-magenta" : "ring-transparent",
          )}
          aria-hidden
        >
          {initialsFor(displayName)}
        </span>
        {/* Under real sign-in the name is not in a picker, so it opens this menu too. */}
        {!isDevMode ? (
          <span className="hidden text-[13px] leading-tight sm:block">
            <span className="block font-bold">{displayName}</span>
            <span className="block text-[11px] text-grey-secondary">{who}</span>
          </span>
        ) : null}
      </button>
      {open ? (
        <div
          id="account-menu"
          className="absolute right-0 top-12 z-50 w-[min(300px,calc(100vw-2rem))] rounded-2xl border border-grey-border bg-white p-2 shadow-op-menu animate-op-pop"
        >
          <div className="border-b border-grey-fill px-3 pb-2.5 pt-1.5">
            <p className="font-bold">{displayName}</p>
            <p className="text-[12px] text-grey-secondary">{accountLine(roles, isDevMode)}</p>
          </div>
          {/* On a phone the header has no room for these, so they are here. */}
          {isDevMode && people.length > 0 ? (
            <div className="border-b border-grey-fill px-3 py-2.5 sm:hidden">
              <p className={menuLabel}>Acting as (local sign-in)</p>
              <ActingAsControls idPrefix="menu" />
            </div>
          ) : null}
          <div className="border-b border-grey-fill px-3 py-2.5 sm:hidden">
            <p className={menuLabel}>Day shown</p>
            <ViewingDateControl />
          </div>
          <div className="pt-1.5">
            {preference.isError && isNoCheckin(preference.error) ? (
              <p className="px-3 py-2.5 text-[13px] text-grey-secondary">{summary}</p>
            ) : (
              <button
                type="button"
                className={item}
                onClick={() => {
                  setOpen(false);
                  setScheduleOpen(true);
                }}
              >
                <span className="font-bold">Your check-in schedule</span>
                <span className="text-[12px] text-grey-secondary">{summary}</span>
              </button>
            )}
            {!isDevMode ? (
              <button type="button" className={item} onClick={() => void logout()}>
                <span className="font-bold text-magenta">Sign out</span>
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
      {/* Outside the menu, so it stays open after the menu closes. */}
      <CheckinScheduleDialog
        open={scheduleOpen}
        onOpenChange={setScheduleOpen}
        returnFocusTo={button}
      />
    </div>
  );
}
