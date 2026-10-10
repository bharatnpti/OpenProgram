import assert from "node:assert/strict";
import { test } from "node:test";

import type { ForecastSettingsResponse } from "../../api/schema";
import {
  MIN_HISTORY_LABEL,
  draftChanged,
  draftFromSettings,
  minHistoryHelp,
  minHistoryProblem,
  minHistoryStatus,
  requestFromDraft,
} from "./forecastForm.ts";

// A new tenant, as GET /config/delivery/forecast returns it.
const DEFAULT: ForecastSettingsResponse = {
  min_history_days: 10,
  default_min_history_days: 10,
  is_default: true,
  lowest: 3,
  highest: 60,
  window_days: 30,
  updated_at: null,
  updated_by: null,
};

test("the field is labelled in plain words and starts at the number in force", () => {
  assert.equal(MIN_HISTORY_LABEL, "Working days of history before a forecast");
  assert.equal(draftFromSettings(DEFAULT), "10");
  assert.equal(draftFromSettings({ min_history_days: 20 }), "20");
  assert.equal(draftChanged("10", DEFAULT), false);
  assert.equal(draftChanged(" 10 ", DEFAULT), false);
  assert.equal(draftChanged("5", DEFAULT), true);
});

test("a value the server would refuse says why, in the server's words", () => {
  assert.equal(minHistoryProblem("10", DEFAULT), null);
  assert.equal(minHistoryProblem("3", DEFAULT), null);
  assert.equal(minHistoryProblem("60", DEFAULT), null);
  assert.equal(minHistoryProblem("", DEFAULT), "Enter a number of working days.");
  assert.equal(
    minHistoryProblem("7.5", DEFAULT),
    "Use a whole number of working days, such as 10.",
  );
  assert.equal(minHistoryProblem("-4", DEFAULT), "Use a whole number of working days, such as 10.");
  // core/domain/forecast.py validated_min_sample_days, word for word.
  assert.equal(
    minHistoryProblem("2", DEFAULT),
    "A forecast needs at least 3 working days of history: with fewer, its 50% and 85% dates replay the same one or two days.",
  );
  assert.equal(
    minHistoryProblem("61", DEFAULT),
    "A forecast can wait for at most 60 working days of history, about three months.",
  );
});

test("the request carries the typed number", () => {
  assert.deepEqual(requestFromDraft(" 15 "), { min_history_days: 15 });
});

test("the help and the status say what the number does and where it comes from", () => {
  assert.equal(
    minHistoryHelp(DEFAULT),
    "The forecast gives its 50% and 85% dates once this many working days of daily snapshots are kept. Fewer give a date sooner but a shakier one; more give a steadier date later. From 3 to 60; the default is 10.",
  );
  assert.equal(
    minHistoryStatus(DEFAULT, null),
    "The default, 10 working days; nothing is saved for this tenant. Forecasts read the last 30 days of snapshots.",
  );
  assert.equal(
    minHistoryStatus(
      { ...DEFAULT, is_default: false, min_history_days: 25, window_days: 38 },
      "Saved 10 Oct, 09:12 by Asha.",
    ),
    "Saved 10 Oct, 09:12 by Asha. Forecasts read the last 38 days of snapshots.",
  );
});
