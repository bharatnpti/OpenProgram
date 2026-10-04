import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { PortfolioFeedItemResponse } from "../../api/schema";
import { entityLabel, feedCard, UNKNOWN_PERSON } from "./feedCard.ts";

// A made-up chat id; the person's name is what the card must show.
const CHAT_ID = "U0123ABCD";

function checkinItem(personName?: string | null): PortfolioFeedItemResponse {
  return {
    source: "checkin",
    kind: "checkin_update",
    summary: "Check-in updated for Rosa Lind: confirmed, 0 blocker(s)",
    entity_ref: { tenant_id: "demo", kind: "developer", id: CHAT_ID },
    observed_at: "2026-10-04T06:06:36Z",
    details: { status_source: "confirmed", blocker_count: 0 },
    ...(personName === undefined ? {} : { person_name: personName }),
  };
}

function cardText(item: PortfolioFeedItemResponse): string[] {
  const card = feedCard(item, 0);
  return [card.title, card.entityLabel, card.ageLabel, card.signalsSay ?? ""];
}

describe("feedCard", () => {
  test("a person's card shows their name, not their chat id", () => {
    // N37: the Signals feed labelled this card "developer · U0123ABCD".
    const item = checkinItem("Rosa Lind");

    assert.equal(feedCard(item, 0).entityLabel, "Rosa Lind");
    for (const text of cardText(item)) {
      assert.ok(!text.includes(CHAT_ID), `card shows the chat id: ${text}`);
    }
  });

  test("a person with no name from the server reads as a team member", () => {
    // An older server sends no field; a blank name is no name.
    for (const item of [checkinItem(), checkinItem(null), checkinItem("  ")]) {
      assert.equal(feedCard(item, 0).entityLabel, UNKNOWN_PERSON);
      for (const text of cardText(item)) {
        assert.ok(!text.includes(CHAT_ID), `card shows the chat id: ${text}`);
      }
    }
  });

  test("an entity that is not a person keeps its kind and id", () => {
    const issue: PortfolioFeedItemResponse = {
      ...checkinItem(null),
      source: "issue",
      kind: "issue_update",
      summary: "Issue CHK-8 moved to done: Payment form validation UI",
      entity_ref: { tenant_id: "demo", kind: "task", id: "CHK-8" },
    };

    assert.equal(feedCard(issue, 0).entityLabel, "task · CHK-8");
  });
});

describe("entityLabel", () => {
  test("a risk filed on a person names them", () => {
    const subject = { entity_ref: { kind: "developer", id: CHAT_ID }, person_name: "Kai Thompson" };

    assert.equal(entityLabel(subject), "Kai Thompson");
    assert.equal(entityLabel({ entity_ref: subject.entity_ref }), UNKNOWN_PERSON);
  });
});
