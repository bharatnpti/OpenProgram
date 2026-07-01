Feature: Mock Slack simulator state survives a backend restart
  Converts MS-E2E-044 of the manual Mock Slack E2E matrix: because the
  simulator store is Redis-backed in container runtime mode, messages must
  survive the backend process being recreated. Requires a real Redis
  container (skips gracefully without Docker).

  @ms_e2e_044
  Scenario: Simulator messages survive a backend container restart
    Given a Redis-backed mock Slack simulator store
    And the simulator has a bot message for user "U1001"
    When the backend process is recreated against the same Redis instance
    Then the persisted simulator message count is 1
    And the simulator still has the bot message for user "U1001"
