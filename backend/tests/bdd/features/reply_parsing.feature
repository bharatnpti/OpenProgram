Feature: Mock Slack reply parsing
  Converts the Reply Parsing rows of the manual Mock Slack E2E matrix into
  automated scenarios. The fake LLM provider always returns a fixed progress
  note, so assertions target the guarantees that hold independent of the
  LLM's output: raw reply text is always persisted verbatim, and whether a
  confirmed status is recorded depends only on deterministic,
  LLM-independent logic.

  @ms_e2e_026
  Scenario: Short complete status parses without clarification
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Finished sprint review, no blockers."
    Then the response status code should be 200
    And the response status should be "processed"
    And the check-in raw reply for "U1001" should equal "Finished sprint review, no blockers."
    And developer "U1001" should have a confirmed status

  @ms_e2e_027
  Scenario: Blocker reply preserves blocker signal
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked by flaky seed test."
    Then the response status code should be 200
    And the response status should be "processed"
    And the check-in raw reply for "U1001" should contain "Blocked by flaky seed test."
    And developer "U1001" should have a confirmed status

  @ms_e2e_028
  Scenario: Ambiguous non-status reply does not become false healthy status
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "ok"
    Then the response status code should be 200
    And the response status should be "acknowledged"
    And the check-in raw reply for "U1001" should be null
    And developer "U1001" should not have a confirmed status

  @ms_e2e_029
  Scenario: Parser JSON failures fall back safely
    Given the LLM provider returns malformed JSON for every call
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Finished API shell; no blockers."
    Then the response status code should be 200
    And the response status should be "clarifying"
    And the check-in raw reply for "U1001" should be null
    And developer "U1001" should not have a confirmed status

  Scenario: Jira contradiction triggers targeted clarification
    Given the LLM provider returns a Jira contradiction clarification
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "PO-1 is done; no blockers; ETA today."
    Then the response status code should be 200
    And the response status should be "clarifying"
    And the latest bot message for "U1001" should contain "PO-1 is still in progress"
    And the check-in raw reply for "U1001" should be null
    And developer "U1001" should not have a confirmed status

  @ms_e2e_042
  Scenario: Long multiline status reply preserves raw formatting
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit a multiline reply to the bot message for "U1001"
    Then the response status code should be 200
    And the response status should be "processed"
    And the check-in raw reply for "U1001" should preserve line breaks
    And developer "U1001" should have a confirmed status
