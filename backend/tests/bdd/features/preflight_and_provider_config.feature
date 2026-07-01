Feature: Mock Slack preflight and provider configuration
  Converts the Preflight and Provider Config rows of the manual Mock Slack
  E2E matrix (mock-slack-e2e-testing.md) into automated scenarios.

  @ms_e2e_001
  Scenario: Simulator enabled local admin preflight
    Given the mock Slack simulator stack is running
    When I request the health and readiness endpoints
    And I request the simulator status
    And I request the simulator messages
    Then the health endpoint reports status "ok"
    And the readiness endpoint reports status "ok"
    And the simulator status response has status code 200
    And the simulator status shows enabled "true" and provider "mock_slack"
    And the simulator messages response has status code 200

  @ms_e2e_002
  Scenario: Full local provider stack avoids real integrations
    Given the mock Slack simulator stack is running
    And a configured member "U1003" named "Priya Nair" with chat id "U1003"
    When I dispatch a check-in for member "U1003"
    Then the dispatch response has status code 200
    And the issue tracker, VCS, and calendar providers are the local fake adapters
    And the simulator has at least 1 message

  @ms_e2e_013
  Scenario: Chat provider mismatch prevents DM capture
    Given the mock Slack simulator stack is running with the chat provider set to "fake"
    Then the simulator is reported unavailable
    And requesting the simulator status returns 404
    And requesting the simulator messages returns 404

  @ms_e2e_014
  Scenario: Invalid provider env fails fast
    Then building settings with chat provider "not-a-real-provider" raises a validation error
    And building settings with directory provider "not-a-real-provider" raises a validation error
    And building settings with issue tracker provider "not-a-real-provider" raises a validation error
    And building settings with vcs provider "not-a-real-provider" raises a validation error
    And building settings with calendar provider "not-a-real-provider" raises a validation error
