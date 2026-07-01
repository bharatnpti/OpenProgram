Feature: Check-in schedule configuration, fanout, and dispatch idempotency
  Converts the Schedule Config, Schedule Fanout, and Idempotency rows of the
  manual Mock Slack E2E matrix into automated scenarios.

  @ms_e2e_003
  Scenario: Daily check-in fanout can be configured to 15 minutes
    Given the compose file is loaded
    Then the "backend" service check-in fanout cron is overrideable via "PULSEOPS_CHECKIN_FANOUT_CRON"
    And the "worker" service check-in fanout cron is overrideable via "PULSEOPS_CHECKIN_FANOUT_CRON"

  @ms_e2e_015
  Scenario: 15-minute fanout dispatches missing eligible members
    Given a check-in workflow registry backed by the mock Slack simulator
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And a configured member "U1002" named "Liam Chen" with chat id "U1002"
    When I run the check-in fanout dispatch for tenant "demo" on "2026-07-06"
    Then the fanout result dispatched count should be 2

  @ms_e2e_016
  Scenario: Same member/date double dispatch does not create second DM
    Given a check-in workflow registry backed by the mock Slack simulator
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    When I run the daily check-in activity for developer "U1001" on "2026-07-06"
    Then the daily check-in result status should be "sent"
    And the daily check-in result should not be marked already recorded
    When I run the daily check-in activity for developer "U1001" on "2026-07-06"
    Then the daily check-in result status should be "sent"
    And the daily check-in result should be marked already recorded
    And the simulator message count is 1

  @ms_e2e_017
  Scenario: Stale skipped_weekend run blocks rerun
    Given a check-in workflow registry backed by the mock Slack simulator
    And a configured member "U1001" named "Asha Rao" with chat id "U1001"
    And developer "U1001" has a "skipped_weekend" schedule run recorded for "2026-07-05"
    When I run the daily check-in activity for developer "U1001" on "2026-07-05"
    Then the daily check-in result status should be "skipped_weekend"
    And the daily check-in result should be marked already recorded
    And no simulator bot message should have been sent
