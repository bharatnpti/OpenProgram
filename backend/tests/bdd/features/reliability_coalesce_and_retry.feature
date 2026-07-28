Feature: Inbound reply coalescing and retry reliability (Plan 01)
  Documents the Plan 01 durable inbound-reply guarantees that both the DBOS
  and Temporal engines depend on:

  - a BURST of inbound messages for one conversation coalesces into a single
    processed reply (the drain reads every buffered event and runs the reply
    pipeline exactly once), and
  - a reply whose processing FAILS stays durably buffered in
    inbound_chat_events and is retried on the next drain, finalizing without
    losing the reply.

  These scenarios drive the real ReplyIngestionService against the in-memory
  inbound_chat_events buffer, so they need no live simulator or workflow
  engine. They mirror the bounded coalesce/drain behavior kept equivalent
  across engines (MAX_DRAIN_PASSES / continue_as_new).

  @plan01_burst_coalesce
  Scenario: A burst of inbound messages coalesces into one processed reply
    Given the mock Slack simulator stack is running
    When a burst of 4 inbound messages is buffered for conversation "burst-thread-1"
    And the conversation "burst-thread-1" is drained once
    Then the drain processes 4 buffered events in a single pass
    And the reply pipeline runs exactly once for the burst
    And the coalesced reply joins all 4 messages oldest to newest

  @plan01_retry_finalizes
  Scenario: A failed reply is retried from the buffer and finalizes without loss
    Given the mock Slack simulator stack is running
    And an inbound reply "Deploy is green, no blockers." is buffered for conversation "retry-thread-1"
    And the reply pipeline fails on its first attempt then succeeds
    When the conversation "retry-thread-1" drain is attempted and fails
    Then the buffered reply is still pending after the failed attempt
    When the conversation "retry-thread-1" is drained again
    Then the drain finalizes the reply without loss
    And the reply pipeline finally received the reply text "Deploy is green, no blockers."
