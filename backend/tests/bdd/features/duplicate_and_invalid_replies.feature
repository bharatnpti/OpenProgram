Feature: Duplicate replies and invalid simulator IDs
  Converts the Duplicate Reply and Invalid IDs rows of the manual Mock Slack
  E2E matrix into automated scenarios.

  @ms_e2e_018
  Scenario: Duplicate direct reply to same bot message
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a confirmed reply "Finished API shell; no blockers."
    When I submit another reply to the bot message for "U1001" with text "Actually still blocked."
    Then the response status code should be 200
    And the response status should be "duplicate"
    And the check-in raw reply for "U1001" should equal "Finished API shell; no blockers."

  @ms_e2e_019
  Scenario: Rapid double submit is safe
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit two concurrent replies to the bot message for "U1001" with texts "Reply A." and "Reply B."
    Then both concurrent replies returned status code 200
    And developer "U1001" should have a confirmed status
    And the check-in raw reply for "U1001" should equal one of "Reply A." or "Reply B."

  @ms_e2e_020
  Scenario: Unknown simulator message ID is rejected
    Given the mock Slack simulator stack is running
    When I submit a reply to message id "does-not-exist-qa-020" with text "Anything."
    Then the response status code should be 404
    And the simulator message count is 0

  @ms_e2e_021
  Scenario: Reply to a user message ID is rejected
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a confirmed reply "Finished API shell; no blockers."
    When I submit a reply to the most recent user message for "U1001" with text "Follow-up that should be rejected."
    Then the response status code should be 404

  @ms_e2e_022
  Scenario: Unknown member dispatch fails safely
    Given the mock Slack simulator stack is running
    When I dispatch a check-in for unknown developer "UQA-UNKNOWN"
    Then the dispatch response has status code 404
    And the simulator message count is 0

  @ms_e2e_023
  Scenario: Bad chat_external_id does not corrupt member status
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And a configured member "U1003" named "Priya Nair" with chat id "U1003"
    When I dispatch a check-in for member "U1003" with chat id "U1001"
    Then the dispatch response has status code 200
    When I submit a reply to the bot message for "U1001" with text "Wrong-target reply."
    Then developer "U1003" should have a confirmed status
    And developer "U1001" should not have a confirmed status
