Feature: Simulator API validation, webhook path, and message contract
  Converts the API Validation, Webhook Path, and Message Contract rows of
  the manual Mock Slack E2E matrix into automated scenarios.

  @ms_e2e_034
  Scenario: Empty reply payload is rejected without side effects
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with an empty payload
    Then the response status code should be 422
    And the simulator message count is 1
    And the check-in raw reply for "U1001" should be null

  @ms_e2e_035
  Scenario: Invalid reply received_at is rejected without side effects
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with an invalid received_at
    Then the response status code should be 422
    And the simulator message count is 1
    And the check-in raw reply for "U1001" should be null

  @ms_e2e_038
  Scenario: Slack-shaped webhook reply processes without the test-support reply endpoint
    Given the mock Slack simulator stack is running
    And a configured member "U1003" named "Priya Nair" with chat id "U1003"
    And member "U1003" has a bot check-in message
    When I post a Slack-shaped webhook reply for "U1003" with text "Finished API shell; no blockers."
    Then the response status code should be 200
    And the response status should be "processed"
    And developer "U1003" should have a confirmed status
    And the simulator message count is 1

  @ms_e2e_039
  Scenario: Simulator message payload contract is complete for bot and user messages
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And member "U1001" has a confirmed reply "Finished API shell; no blockers."
    Then every simulator bot message has purpose and correlation metadata
    And every simulator user message has reply_to_message_id and mock_slack source metadata
