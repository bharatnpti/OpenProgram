// What Ask now says, pure: no imports, so `node --test` runs it as written.

/** The button's name, the yes step's words, and the line once it went. */
export function askNowWords(name: string): {
  button: string;
  title: string;
  effect: string;
  done: string;
} {
  const first = name.trim().split(/\s+/)[0] || name;
  return {
    button: `Ask ${name} for a check-in now`,
    title: `Ask ${first} for a check-in now?`,
    effect: `Ora sends ${first} today's check-in in your chat workspace now, as the scheduled one does. Their reply counts as today's.`,
    done: `Check-in requested. Ora asks ${first} in a moment.`,
  };
}
