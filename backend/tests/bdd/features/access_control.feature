Feature: Mock Slack simulator access control
  Converts the Access Control rows of the manual Mock Slack E2E matrix into
  automated scenarios.

  @ms_e2e_009
  Scenario: Non-admin cannot use simulator API
    Given the mock Slack simulator stack is running with a non-admin caller
    When I request the simulator status
    Then the simulator status response has status code 403
    When I request the simulator messages
    Then the simulator messages response has status code 403
    When I reset the simulator
    Then the response status code should be 403

  @ms_e2e_010
  Scenario: Simulator disabled returns not found
    Given the mock Slack simulator is disabled
    Then requesting the simulator status returns 404

  @ms_e2e_011
  Scenario: Non-local environment returns not found
    Given the backend environment is not local
    Then requesting the simulator status returns 404
    And requesting the simulator messages returns 404
