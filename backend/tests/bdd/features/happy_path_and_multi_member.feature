Feature: Mock Slack happy path and multi-member correlation
  Converts the Happy Path and Multi-Member rows of the manual Mock Slack E2E
  matrix into automated scenarios.

  @ms_e2e_005
  Scenario: Dispatch and reply confirm member status
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    When I dispatch a check-in for member "U1001"
    Then a bot message should be recorded for member "U1001"
    When I submit a reply to the bot message for "U1001" with text "Finished API shell; no blockers."
    Then the response status code should be 200
    And the response status should be "processed"
    And the check-in raw reply for "U1001" should equal "Finished API shell; no blockers."
    And developer "U1001" should have a confirmed status

  @ms_e2e_024
  Scenario: Replies correlate to the correct member
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And a configured member "U1002" named "Liam Chen" with chat id "U1002"
    When I dispatch a check-in for member "U1001"
    And I dispatch a check-in for member "U1002"
    Then a bot message should be recorded for member "U1001"
    And a bot message should be recorded for member "U1002"
    When I submit a reply to the bot message for "U1001" with text "Asha finished the API shell."
    And I submit a reply to the bot message for "U1002" with text "Liam finished the schema review."
    Then the check-in raw reply for "U1001" should equal "Asha finished the API shell."
    And the check-in raw reply for "U1002" should equal "Liam finished the schema review."
    And developer "U1001" should have a confirmed status
    And developer "U1002" should have a confirmed status
