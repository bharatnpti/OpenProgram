Feature: Mock Slack simulator state, reset, and post-reset dispatch
  Converts the Simulator State and Reset Persistence rows of the manual Mock
  Slack E2E matrix into automated scenarios.

  @ms_e2e_006
  Scenario: Refresh does not duplicate messages
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I request the simulator messages
    Then the simulator message count is 1
    When I request the simulator messages
    Then the simulator message count is 1

  @ms_e2e_007
  Scenario: Reset clears simulator mailbox only
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a confirmed reply "Finished API shell; no blockers."
    When I reset the simulator
    Then the response status code should be 204
    And the simulator message count is 0
    And developer "U1001" should have a confirmed status

  @ms_e2e_008
  Scenario: Reply after reset rejects stale message ID
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    And the simulator has been reset
    When I submit another reply to the bot message for "U1001" with text "Still working on it."
    Then the response status code should be 404
    And the simulator message count is 0

  @ms_e2e_030
  Scenario: Reset after confirmed reply preserves app status
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a confirmed reply "Finished API shell; no blockers."
    When I reset the simulator
    Then the simulator message count is 0
    And developer "U1001" should have a confirmed status

  @ms_e2e_041
  Scenario: Post-reset future-date dispatch reopens mock channel cleanly
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    And the simulator has been reset
    When I dispatch a check-in for member "U1001" on date "2099-07-06"
    Then the dispatch response has status code 200
    And the simulator message count is 1
