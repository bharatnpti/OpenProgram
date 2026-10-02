Feature: Cross-person request detection
  Detects named dependencies in check-in replies, resolves them through the
  directory, notifies the counterpart (on by default), and routes counterpart
  replies back into the request lifecycle.

  Scenario: Directory-resolved request notifies the counterpart by default and records acknowledgement
    Given the cross-person request stack is running with Liam review extraction
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked waiting on Liam Chen to review the API schema."
    Then the response status code should be 200
    And the response status should be "processed"
    And a cross-person request should notify "U1002" with text containing "API schema review"
    And a cross-person request should notify "U1002" with text containing "Asha Rao asked for your review"
    And the cross-person request DM to "U1002" should not contain "Blocked waiting"
    And the cross-person request DM to "U1002" should not contain "U1001"
    When member "U1002" replies to the cross-person request with text "on it"
    Then the response status code should be 200
    And the response status should be "acknowledged"
    And the cross-person request for "U1002" should have status "acknowledged"

  Scenario: Directory-resolved request is recorded without a counterpart DM when auto notify is switched off
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

  Scenario: The counterpart's threaded reply resolves the request and the requester is told
    Given the cross-person request stack is running with Liam review extraction
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked waiting on Liam Chen to review the API schema."
    Then the response status should be "processed"
    When member "U1002" replies in the thread of the cross-person request with text "Reviewed and approved"
    Then the response status code should be 200
    And the response status should be "resolved"
    And the cross-person request for "U1002" should have status "resolved"
    And "U1001" should be told the cross-person request was resolved by "Liam Chen"

  Scenario: The counterpart answers from the built-in chat's Reply action without opening a check-in
    Given the cross-person request stack is running with Liam review extraction
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked waiting on Liam Chen to review the API schema."
    Then the response status should be "processed"
    When member "U1002" answers the cross-person request from the built-in chat thread with text "on it"
    Then the response status code should be 200
    And the response status should be "acknowledged"
    And no check-in should have been opened for "U1002"
    When member "U1002" answers the cross-person request from the built-in chat thread with text "Done, reviewed and approved"
    Then the response status should be "resolved"
    And the cross-person request for "U1002" should have status "resolved"
    And no check-in should have been opened for "U1002"
    And "U1001" should be told the cross-person request was resolved by "Liam Chen"

  Scenario: A person who cannot be found gets no DM and the request stays with the requester
    Given the cross-person request stack is running where every reply names "Zed Quinn"
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Waiting on Zed Quinn for the API schema."
    Then the response status should be "clarifying"
    And the latest bot message for "U1001" should contain "Zed Quinn"
    When I reply to the latest bot message for "U1001" with text "Zed Quinn, as I said."
    Then the response status should be "clarifying"
    When I reply to the latest bot message for "U1001" with text "Still Zed Quinn."
    Then the response status should be "processed"
    And the cross-person request raised by "U1001" should have status "needs_resolution"
    And no cross-person request should notify anyone

  Scenario: An ambiguous name that is never settled gets no DM and the request stays with the requester
    Given the cross-person request stack is running where every reply names "Alex"
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the directory contains ambiguous Alex users
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Waiting on Alex for the API schema."
    Then the response status should be "clarifying"
    And the latest bot message for "U1001" should contain "alexa.roy@example.com"
    When I reply to the latest bot message for "U1001" with text "Alex."
    Then the response status should be "clarifying"
    When I reply to the latest bot message for "U1001" with text "Just Alex."
    Then the response status should be "processed"
    And the cross-person request raised by "U1001" should have status "needs_resolution"
    And no cross-person request should notify anyone

  Scenario: A redelivered check-in reply does not DM the counterpart twice
    Given the cross-person request stack is running with Liam review extraction
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When Slack delivers the check-in reply event "Ev-1" from "U1001" with text "Blocked waiting on Liam Chen to review the API schema."
    Then the response status should be "processed"
    And exactly 1 cross-person request DM should have gone to "U1002"
    When Slack delivers the check-in reply event "Ev-1" from "U1001" with text "Blocked waiting on Liam Chen to review the API schema."
    Then the response status should be "duplicate"
    And exactly 1 cross-person request DM should have gone to "U1002"

  Scenario: Naming yourself sends no DM but keeps the request
    Given the cross-person request stack is running where every reply names "Asha Rao"
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked waiting on Asha Rao to review the API schema."
    Then the response status should be "processed"
    And the cross-person request raised by "U1001" should have status "open"
    And no cross-person request should notify anyone

  Scenario: A thank-you after the request is resolved neither reopens it nor tells the requester again
    Given the cross-person request stack is running with Liam review extraction
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And the mock Slack directory is synced
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "Blocked waiting on Liam Chen to review the API schema."
    Then the response status should be "processed"
    When member "U1002" replies in the thread of the cross-person request with text "Reviewed and approved"
    Then the response status should be "resolved"
    When member "U1002" replies in the thread of the cross-person request with text "thanks!"
    Then the response status should be "resolved"
    And the cross-person request for "U1002" should have status "resolved"
    And "U1001" should be told the cross-person request was resolved by "Liam Chen"
