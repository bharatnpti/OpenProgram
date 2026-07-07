Feature: Mock Slack frontend behavior
  Converts the pure frontend/browser rows of the manual Mock Slack E2E
  matrix that cannot be exercised through the backend API alone: role-based
  navigation gating, route guards, disabled-button validation, polling,
  auto-select behavior, and timeline grouping.

  These scenarios drive a real Vite dev server and backend through
  Playwright and only run with OPENPROGRAM_RUN_UI_BDD=1 (see `make ui-bdd`);
  they skip gracefully otherwise.

  @ms_e2e_012 @ui_bdd_scenario
  Scenario: Mock Slack navigation is admin/local gated
    Given the Mock Slack frontend stack is running
    When I open "/me" as role "admin"
    Then the navigation should show a "Mock Slack" link
    And the navigation should show a "Admin Config" link
    When I switch the active role to "Developer"
    Then the navigation should not show a "Mock Slack" link
    And the navigation should not show a "Admin Config" link

  @ms_e2e_043 @ui_bdd_scenario
  Scenario: Already-mounted Mock Slack route is gated when switching to Developer role
    Given the Mock Slack frontend stack is running
    When I open "/mock-slack" as role "admin"
    Then the browser URL path should be "/mock-slack"
    When I switch the active role to "Developer"
    Then the browser URL path should be "/me"
    And the navigation should not show a "Mock Slack" link

  @ms_e2e_033 @ui_bdd_scenario
  Scenario: Blank or whitespace-only reply cannot be submitted
    Given the Mock Slack frontend stack is running
    And a configured member "U1001" named "Asha Rao" exists
    And member "U1001" has a dispatched bot check-in
    When I open "/mock-slack" as role "admin"
    And I enter whitespace-only text into the reply message field
    Then the "Submit reply" button should be disabled

  @ms_e2e_036 @ui_bdd_scenario
  Scenario: Reset clears selected reply target and prevents stale UI reply
    Given the Mock Slack frontend stack is running
    And a configured member "U1001" named "Asha Rao" exists
    And member "U1001" has a dispatched bot check-in
    When I open "/mock-slack" as role "admin"
    And I reset the simulator through the UI
    Then the reply message selector should show "Select message"
    And the "Submit reply" button should be disabled

  @ms_e2e_037 @ui_bdd_scenario
  Scenario: New bot message auto-selects only when no reply target is selected
    Given the Mock Slack frontend stack is running
    And a configured member "U1001" named "Asha Rao" exists
    When I open "/mock-slack" as role "admin"
    Then the reply message selector should show "Select message"
    When member "U1001" has a dispatched bot check-in
    And I wait for the message timeline to refresh
    Then the reply message selector should no longer show "Select message"

  @ms_e2e_025 @ui_bdd_scenario
  Scenario: Newest bot message selection is intentional
    Given the Mock Slack frontend stack is running
    And a configured member "U1001" named "Asha Rao" exists
    And a configured member "U1002" named "Liam Chen" exists
    And member "U1001" has a dispatched bot check-in
    When I open "/mock-slack" as role "admin"
    Then the reply message selector should show a message for user "U1001"
    When member "U1002" has a dispatched bot check-in
    And I wait for the message timeline to refresh
    Then the reply message selector should still show a message for user "U1001"

  @ms_e2e_031 @ui_bdd_scenario
  Scenario: Message polling surfaces delayed DM
    Given the Mock Slack frontend stack is running
    And a configured member "U1001" named "Asha Rao" exists
    When I open "/mock-slack" as role "admin"
    Then the Messages KPI should read "0"
    When member "U1001" has a dispatched bot check-in
    And I wait for the message timeline to refresh without clicking Refresh
    Then the Messages KPI should read "1"

  @ms_e2e_032 @ui_bdd_scenario
  Scenario: Member list refresh behavior is known
    Given the Mock Slack frontend stack is running
    And a configured member "U1001" named "Asha Rao" exists
    And a configured member "U1002" named "Liam Chen" exists
    When I open "/mock-slack" as role "admin"
    Then the Members KPI should read "2"
    When I click the Refresh button
    Then the Members KPI should read "2"

  @ms_e2e_040 @ui_bdd_scenario
  Scenario: Timeline groups multiple Slack users with counts matching API
    Given the Mock Slack frontend stack is running
    And a configured member "U1001" named "Asha Rao" exists
    And a configured member "U1002" named "Liam Chen" exists
    And member "U1001" has a dispatched bot check-in
    And member "U1002" has a dispatched bot check-in
    When I open "/mock-slack" as role "admin"
    Then the timeline should show one group for user "U1001" with 1 message
    And the timeline should show one group for user "U1002" with 1 message
