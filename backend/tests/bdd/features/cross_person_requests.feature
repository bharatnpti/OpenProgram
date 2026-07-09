Feature: Cross-person request detection
  Detects named dependencies in check-in replies, resolves them through the
  directory, notifies the counterpart, and routes counterpart replies back into
  the request lifecycle.

  Scenario: Directory-resolved request notifies the counterpart and records acknowledgement
    Given the cross-person request stack is running with Liam review extraction
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked waiting on Liam Chen to review the API schema."
    Then the response status code should be 200
    And the response status should be "processed"
    And a cross-person request should notify "U1002" with text containing "API schema review"
    When member "U1002" replies to the cross-person request with text "on it"
    Then the response status code should be 200
    And the response status should be "acknowledged"
    And the cross-person request for "U1002" should have status "acknowledged"

  Scenario: Directory-resolved request records without automatic counterpart DM by default
    Given the cross-person request stack is running with Liam review extraction and auto notify disabled
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked waiting on Liam Chen to review the API schema."
    Then the response status code should be 200
    And the response status should be "processed"
    And the cross-person request for "U1002" should have status "open"
    And no cross-person request should notify "U1002"

  Scenario: Ambiguous name is clarified by email before notifying the counterpart
    Given the cross-person request stack is running with ambiguous Alex responses
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the directory contains ambiguous Alex users
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked waiting on Alex for schema confirmation."
    Then the response status code should be 200
    And the response status should be "clarifying"
    And the latest bot message for "U1001" should contain "alex.chen@example.com"
    And the latest bot message for "U1001" should contain "alexa.roy@example.com"
    And no cross-person requests should be recorded
    When I reply to the latest bot message for "U1001" with text "I meant alexa.roy@example.com."
    Then the response status code should be 200
    And the response status should be "processed"
    And a cross-person request should notify "U2002" with text containing "schema confirmation"
