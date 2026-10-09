// No imports: this module runs under `node --test` as written.

/*
 * Browsers report a few time zones under the names they had years ago
 * ("Asia/Calcutta" for India, "Europe/Kiev"). A server built on a current time
 * zone database (Debian's, since 13, keeps the old names in a separate
 * package) does not know them: the portfolio verdict then quietly answered in
 * UTC, and a time zone saved from this device would be refused or differ from
 * the name already stored ("Asia/Kolkata"). So a zone read from the browser is
 * always sent under its current name.
 */
const CURRENT_NAME: Record<string, string> = {
  "Africa/Asmera": "Africa/Asmara",
  "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
  "America/Catamarca": "America/Argentina/Catamarca",
  "America/Cordoba": "America/Argentina/Cordoba",
  "America/Godthab": "America/Nuuk",
  "America/Indianapolis": "America/Indiana/Indianapolis",
  "America/Jujuy": "America/Argentina/Jujuy",
  "America/Louisville": "America/Kentucky/Louisville",
  "America/Mendoza": "America/Argentina/Mendoza",
  "Asia/Calcutta": "Asia/Kolkata",
  "Asia/Dacca": "Asia/Dhaka",
  "Asia/Katmandu": "Asia/Kathmandu",
  "Asia/Macao": "Asia/Macau",
  "Asia/Rangoon": "Asia/Yangon",
  "Asia/Saigon": "Asia/Ho_Chi_Minh",
  "Asia/Thimbu": "Asia/Thimphu",
  "Asia/Ujung_Pandang": "Asia/Makassar",
  "Asia/Ulan_Bator": "Asia/Ulaanbaatar",
  "Atlantic/Faeroe": "Atlantic/Faroe",
  "Europe/Kiev": "Europe/Kyiv",
  "Pacific/Ponape": "Pacific/Pohnpei",
  "Pacific/Truk": "Pacific/Chuuk",
};

/** The current name of a time zone: "Asia/Calcutta" is "Asia/Kolkata"; any other name is as given. */
export function currentZoneName(zone: string): string {
  return CURRENT_NAME[zone] ?? zone;
}

/** The zone this browser runs in, under its current name; null when the browser doesn't say. */
export function deviceTimezone(): string | null {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return zone ? currentZoneName(zone) : null;
  } catch {
    return null;
  }
}

/** Where a new day report's zone comes from, for the line under the field. */
export type ReportZoneFrom = "team" | "device" | "none";

/**
 * The zone a new day report starts with: the team's, which is the one its
 * check-ins already run in; else this device's, when the team's is not known (a
 * sign-in with no member record); else UTC. A report goes to a team, not to the
 * browser that happens to set it up: Ira, setting one up for a Berlin team from
 * a laptop in India, was offered the laptop's zone.
 */
export function reportZone(
  team: string | null | undefined,
  device: string | null | undefined,
): { zone: string; from: ReportZoneFrom } {
  const teamZone = team?.trim();
  if (teamZone) return { zone: teamZone, from: "team" };
  const deviceZone = device?.trim();
  if (deviceZone) return { zone: deviceZone, from: "device" };
  return { zone: "UTC", from: "none" };
}

/**
 * An instant as the wall clock reads in `zone`, with the zone named after it the
 * way a schedule names its own: "Mon 5 Oct 17:30 Europe/Berlin". A report's last
 * send sits beside its schedule ("18:00 Europe/Berlin"), so it is read in that
 * zone and says so, not in the viewer's with nothing said. A zone this browser
 * does not know is read as UTC, and says UTC. "—" when there is no time.
 */
export function formatInZone(iso: string | null | undefined, zone: string): string {
  if (!iso) return "—";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "—";
  const clock = (timeZone: string) => {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(at);
    const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
    return `${part("weekday")} ${part("day")} ${part("month")} ${part("hour")}:${part("minute")}`;
  };
  try {
    return `${clock(zone)} ${zone}`;
  } catch {
    return `${clock("UTC")} UTC`;
  }
}
