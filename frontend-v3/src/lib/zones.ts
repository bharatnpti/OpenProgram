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
