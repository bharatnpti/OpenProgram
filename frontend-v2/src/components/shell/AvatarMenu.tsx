import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check } from "lucide-react";

import { useRole } from "../../app/role";
import { appRoles, roleLabels, type AppRole } from "../../app/role";
import { cn } from "../../lib/utils";

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/);
  const initials = parts.slice(0, 2).map((part) => part[0]?.toUpperCase() ?? "");
  return initials.join("") || "OP";
}

export function AvatarMenu() {
  const { role, setRole, roleLabel, isDevMode, canAccessAdmin, user } = useRole();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const displayName = user?.name ?? user?.username ?? roleLabel;
  const initials = initialsFor(displayName);

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        aria-label="Account menu"
        onClick={() => setOpen((current) => !current)}
        className={cn(
          "grid h-12 w-12 place-items-center rounded-full bg-ink text-[15px] font-bold text-white ring-2",
          open ? "ring-magenta" : "ring-white",
        )}
      >
        {initials}
      </button>
      {open ? (
        <div className="animate-op-pop absolute right-0 top-14 w-[280px] rounded-2xl border border-grey-border bg-white p-2 shadow-op-menu">
          <div className="border-b border-grey-fill px-3.5 py-3">
            <div className="font-bold">{displayName}</div>
            <div className="text-[13px] text-grey-secondary">{roleLabel} · demo tenant</div>
          </div>
          <div className="px-3.5 pb-1 pt-2.5 text-xs font-bold uppercase tracking-wide text-grey-secondary">
            View as
          </div>
          {appRoles.map((item) => (
            <button
              key={item}
              type="button"
              disabled={!isDevMode}
              onClick={() => setRole(item)}
              className={cn(
                "flex w-full items-center justify-between rounded-lg px-3.5 py-2.5 text-left text-sm hover:bg-grey-fill disabled:cursor-default disabled:hover:bg-transparent",
                item === role ? "font-bold text-magenta" : "text-ink",
              )}
            >
              {roleLabelFor(item)}
              {item === role ? <Check size={16} /> : null}
            </button>
          ))}
          {canAccessAdmin ? (
            <div className="mt-1.5 border-t border-grey-fill pt-1.5">
              <button
                type="button"
                onClick={() => {
                  navigate("/admin");
                  setOpen(false);
                }}
                className="flex w-full items-center rounded-lg px-3.5 py-2.5 text-left text-sm hover:bg-grey-fill"
              >
                Admin configuration
              </button>
              <button
                type="button"
                onClick={() => {
                  navigate("/sim");
                  setOpen(false);
                }}
                className="flex w-full items-center rounded-lg px-3.5 py-2.5 text-left text-sm hover:bg-grey-fill"
              >
                Chat simulator
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function roleLabelFor(role: AppRole): string {
  return roleLabels[role];
}
