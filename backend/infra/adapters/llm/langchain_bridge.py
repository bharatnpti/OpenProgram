"""A LangChain chat model that calls OpenProgram's own LlmProvider.

An agent harness built on LangChain (deepagents) takes any ``BaseChatModel``.
This one sends every completion through the provider the rest of the app uses,
so the configured gateway, its retries, the Langfuse traces and the test fakes
all apply, and no LangChain provider package is needed.

LangChain's conversation is sent as it went (``LlmRequest.turns``): every user,
assistant and tool message in order after the system text, so a note the harness
adds after the tool results is read after them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.language_models.base import LangSmithParams
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, SkipValidation

from core.domain.graph import JsonScalar
from core.domain.llm import LlmRequest, LlmResponse, LlmTool, LlmToolCall, LlmTurn
from core.ports.llm import LlmProvider

# The provider name harness profiles are registered under for this model.
PROVIDER = "openprogram"


class OpenProgramChatModel(BaseChatModel):
    """One model, for one purpose of one run: who asked, and what it is for."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # A Protocol, so not a type pydantic can check an instance against.
    provider: SkipValidation[LlmProvider]
    model_name: str
    tenant_id: str
    correlation_id: str
    metadata_fields: dict[str, JsonScalar] = {}

    @property
    def _llm_type(self) -> str:
        return PROVIDER

    def _get_ls_params(
        self,
        stop: list[str] | None = None,
        **kwargs: Any,  # noqa: ANN401 -- LangChain's signature
    ) -> LangSmithParams:
        return LangSmithParams(
            ls_provider=PROVIDER, ls_model_name=self.model_name, ls_model_type="chat"
        )

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,  # noqa: ANN401 -- LangChain's signature
    ) -> Runnable[LanguageModelInput, AIMessage]:
        # The provider has no tool_choice: the model always chooses.
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,  # noqa: ANN401 -- LangChain's signature
    ) -> ChatResult:
        raise NotImplementedError("OpenProgramChatModel is async only: use ainvoke or astream")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,  # noqa: ANN401 -- LangChain's signature
    ) -> ChatResult:
        request = to_request(
            messages,
            tools=kwargs.get("tools") or (),
            model=self.model_name,
            tenant_id=self.tenant_id,
            correlation_id=self.correlation_id,
            metadata=self.metadata_fields,
        )
        response = await self.provider.complete(request)
        return ChatResult(generations=[ChatGeneration(message=to_message(response))])


def to_request(
    messages: Sequence[BaseMessage],
    *,
    tools: Sequence[Mapping[str, Any]],
    model: str,
    tenant_id: str,
    correlation_id: str,
    metadata: Mapping[str, JsonScalar],
) -> LlmRequest:
    system: list[str] = []
    turns: list[LlmTurn] = []
    for message in messages:
        text = _text(message.content)
        if isinstance(message, SystemMessage):
            system.append(text)
        elif isinstance(message, HumanMessage):
            turns.append(LlmTurn(role="user", content=text))
        elif isinstance(message, AIMessage):
            calls = tuple(_tool_call(call) for call in message.tool_calls)
            turns.append(LlmTurn(role="assistant", content=text, tool_calls=calls))
        elif isinstance(message, ToolMessage):
            turns.append(LlmTurn(role="tool", content=text, tool_call_id=message.tool_call_id))
    return LlmRequest(
        tenant_id=tenant_id,
        prompt="",
        model=model,
        correlation_id=correlation_id,
        system="\n\n".join(part for part in system if part) or None,
        tools=tuple(_tool(tool) for tool in tools),
        turns=tuple(turns),
        metadata=dict(metadata),
    )


def to_message(response: LlmResponse) -> AIMessage:
    usage = response.usage
    return AIMessage(
        content=response.text,
        tool_calls=[
            ToolCall(name=call.name, args=_arguments(call), id=call.id, type="tool_call")
            for call in response.tool_calls
        ],
        usage_metadata=UsageMetadata(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        ),
        response_metadata={
            "model_name": response.model,
            "trace_id": response.trace_id,
            "finish_reason": response.finish_reason,
        },
    )


def _tool(tool: Mapping[str, Any]) -> LlmTool:
    function = tool.get("function", tool)
    return LlmTool(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        parameters=dict(function.get("parameters") or {"type": "object", "properties": {}}),
    )


def _tool_call(call: ToolCall) -> LlmToolCall:
    args = call["args"]
    return LlmToolCall(
        id=call["id"] or "",
        name=call["name"],
        arguments={
            key: value
            for key, value in args.items()
            if value is None or isinstance(value, str | int | float | bool)
        },
        arguments_json=json.dumps(args, ensure_ascii=False),
    )


def _arguments(call: LlmToolCall) -> dict[str, Any]:
    """The call's arguments as the model wrote them, lists and objects too."""
    if call.arguments_json:
        try:
            decoded = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            return decoded
    return dict(call.arguments)


def _text(content: str | list[str | dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    parts = (
        part if isinstance(part, str) else str(part.get("text") or "")
        for part in content
        if isinstance(part, str) or part.get("type") == "text"
    )
    return "".join(parts)
