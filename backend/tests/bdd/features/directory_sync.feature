Feature: Mock Slack directory sync
  Converts the Directory rows of the manual Mock Slack E2E matrix into
  automated scenarios.

  @ms_e2e_004
  Scenario: Mock directory sync exposes stable users
    Given the mock Slack simulator stack is running
    When I sync the directory
    Then the response status code should be 200
    And the directory sync reports 3 synced users

  @ms_e2e_045
  Scenario: Directory sync is idempotent when run repeatedly
    Given the mock Slack simulator stack is running
    When I sync the directory
    And I sync the directory
    Then both directory syncs reported 3 synced users and 0 deactivated
    And the config directory has 3 total users
