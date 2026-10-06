import { ChevronDown } from "lucide-react";

import { roleLabels, useRole, type AppRole } from "../../app/role";
import { groupPeople, personOption, rolesLabel } from "../../app/roleWords";

/**
 * Local demo tenants only: act as any seeded person, and pick the lens for
 * people who hold several roles. Choosing re-issues every request as them.
 *
 * A native select, so it works by keyboard and with a phone's own picker,
 * grouped by each person's main role. Its face, drawn underneath, shows the
 * name and every role held rather than a truncated "Name · Title".
 *
 * Shown in the header, and in the account menu on a phone; `idPrefix` keeps
 * the two copies' ids apart.
 */
export function ActingAsControls({ idPrefix }: { idPrefix: string }) {
  const { people, actingAs, setActingAsId, roles, role, setRole } = useRole();
  const groups = groupPeople(people);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-0">
        <select
          id={`${idPrefix}-acting-as`}
          aria-label="Acting as"
          className="peer absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0"
          value={actingAs?.id ?? ""}
          onChange={(event) => setActingAsId(event.target.value)}
        >
          {actingAs ? null : <option value="">Pick a person</option>}
          {groups.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.people.map((person) => (
                <option key={person.id} value={person.id}>
                  {personOption(person)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <div
          aria-hidden
          className="flex h-10 max-w-[240px] items-center gap-2 rounded-full border border-grey-border bg-white pl-4 pr-2.5 peer-hover:border-grey-disabled peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-magenta"
        >
          <span className="min-w-0 leading-tight">
            <span className="block truncate text-[13px] font-bold">
              {actingAs?.name ?? "Pick a person"}
            </span>
            <span className="block truncate text-[11px] text-grey-secondary">
              Acting as · {rolesLabel(actingAs?.roles)}
            </span>
          </span>
          <ChevronDown size={14} className="flex-none text-grey-secondary" />
        </div>
      </div>
      {roles.length > 1 ? (
        <>
          <label htmlFor={`${idPrefix}-lens`} className="sr-only">
            View as
          </label>
          <select
            id={`${idPrefix}-lens`}
            className="h-10 rounded-full border border-grey-border bg-white px-3 text-[13px] font-bold"
            value={role}
            onChange={(event) => setRole(event.target.value as AppRole)}
          >
            {roles.map((item) => (
              <option key={item} value={item}>
                View as {roleLabels[item]}
              </option>
            ))}
          </select>
        </>
      ) : null}
    </div>
  );
}
