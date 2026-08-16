Feature: Blocker lifecycle and pod attribution
  A developer can belong to multiple pods, so blockers are first-class
  lifecycle records with optional work-item/pod attribution. A multi-pod
  developer with an unattributed blocker gets exactly one attribution
  question; the answer routes the blocker to the right pod board. Non-response
  outcomes never touch lifecycle rows, so blocker age stays honest.

  @blocker_lifecycle_attribution
  Scenario: Multi-pod developer attributes a blocker after one clarification
    Given the mock Slack simulator stack is running
    And the LLM provider is scripted for a mixed attributed and unattributed blocker conversation
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And developer "U1001" belongs to pods "Checkout" and "Payments"
    And member "U1001" has a bot check-in message
    When I submit a reply to the bot message for "U1001" with text "PAY-7 blocked on vendor API; also waiting on staging DB access. No ETA change."
    Then the response status code should be 200
    And the response status should be "clarifying"
    And the latest outbound DM to "U1001" should mention "staging DB access"
    And the latest outbound DM to "U1001" should mention "Checkout"
    And developer "U1001" should have 2 open blockers
    When I submit a reply to the bot message for "U1001" with text "the staging DB one is Checkout"
    Then the response status code should be 200
    And the response status should be "processed"
    And blocker "vendor API" for "U1001" should be attributed to work item "PAY-7"
    And blocker "staging DB access" for "U1001" should be attributed to pod "Checkout"

  @blocker_lifecycle_stale_carry
  Scenario: Non-response leaves the blocker lifecycle untouched
    Given the mock Slack simulator stack is running
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And developer "U1001" has an open blocker "waiting on staging DB access" first seen 2 days ago
    And member "U1001" has a bot check-in message
    When the check-in for "U1001" is closed as a non-response
    Then the developer status source for "U1001" should be "stale"
    And blocker "waiting on staging DB access" for "U1001" should remain open with unchanged last_seen
