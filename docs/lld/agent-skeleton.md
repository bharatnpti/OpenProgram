# Agent Skeleton LLD

## Classes

- `StatusAgentNode` in `core.application.agents.status_agent`.
- `LlmProvider` Protocol in `core.ports.llm`.
- `LiteLlmProvider` in `infra.adapters.llm`.

## Flow

```mermaid
sequenceDiagram
    participant App as StatusAgentNode
    participant Port as LlmProvider
    participant Gateway as LiteLLM
    participant Trace as Langfuse
    App->>Port: complete(LlmRequest)
    Port->>Gateway: HTTP completion request
    Gateway-->>Port: completion + usage
    Port->>Trace: trace metadata
    Port-->>App: LlmResponse
```

## Trace Requirements

Every call records prompt hash, token counts, cost, latency, tenant, and correlation ID. Prompts are not written to normal logs.

## Tests

Unit tests use a fake `LlmProvider`. Integration tests can point `LiteLlmProvider` at a local LiteLLM gateway and Langfuse.
