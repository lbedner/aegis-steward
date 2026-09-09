"""The streaming chat entrypoint.

Same turn shape as ``chat()`` one file over, but yielding
``StreamingMessage`` chunks as the model produces them, with usage
captured after the stream drains.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
import uuid

from pydantic_ai.exceptions import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.run import AgentRunResultEvent

from app.core.log import logger
from app.services.ai.domains.chat.agent_loader import (
    DEFAULT_AGENT_SLUG,
    resolve_agent,
)
from app.services.ai.domains.chat.attachments import (
    ChatAttachment,
    build_user_content,
    prepare_turn,
)
from app.services.ai.domains.chat.module_context import render_memory_modules
from app.services.ai.domains.chat.readings import (
    merge_staged_readings,
    reading_stage,
)
from app.services.ai.domains.chat.user_memory import (
    build_user_memory_context,
    memory_user,
)
from app.services.ai.domains.llm.providers import ProviderNotInstalledError
from app.services.ai.models import (
    MessageRole,
    StreamingConversation,
    StreamingMessage,
)
from app.services.ai.service.base import (
    AIServiceError,
    ProviderError,
)
from app.services.ai.service.chat import ChatMixin
from app.services.ai.service.prompt import history_char_budget
from app.services.ai.service.trace import (
    call_args_preview,
    nested_tool_calls,
    record_tool_call,
    record_tool_result,
)


class StreamingMixin(ChatMixin):
    """Streaming variant of the chat turn."""

    @staticmethod
    def _tool_notice(
        tool_name: str,
        *,
        message_id: str,
        conversation_id: str | None,
        stream_delta: bool,
        args: str = "",
    ) -> StreamingMessage:
        """A content-free chunk naming a tool call, for the UI trail."""
        return StreamingMessage(
            content="",
            is_final=False,
            is_delta=stream_delta,
            message_id=message_id,
            conversation_id=conversation_id,
            metadata={"event": "tool", "tool": tool_name, "args": args},
        )

    async def stream_chat(
        self,
        message: str,
        conversation_id: str | None = None,
        user_id: str = "default",
        stream_delta: bool = False,
        agent_slug: str | None = None,
        surface: str | None = None,
        attachments: list[ChatAttachment] | None = None,
    ) -> AsyncIterator[StreamingMessage]:
        """
        Stream a chat message with real-time response generation.

        Args:
            message: The user's message
            conversation_id: Optional conversation ID (creates new if None)
            user_id: User identifier for conversation ownership
            stream_delta: Whether to stream delta changes or full content
            agent_slug: Agent row to speak as (None = the default agent)
            surface: Originating chat surface, recorded on new
                conversations so each embedded chat lists only its own
                history

        Yields:
            StreamingMessage: Real-time message chunks

        Raises:
            AIServiceError: If service is disabled or not configured
            ProviderError: If AI provider fails
            ConversationError: If conversation management fails
        """
        if not self.config.enabled:
            raise AIServiceError("AI service is disabled")

        try:
            # Get fresh config for metadata (reflects current .env after use-provider)
            current = self.config

            # Resolve the requested agent definition (DB row or code default)
            if agent_slug:
                agent_config = await resolve_agent(agent_slug)
            else:
                agent_config = await resolve_agent()

            # Setup conversation and add user message. The stored text
            # carries the attachment marker; the bytes ride only this
            # turn's model call (history replays as text).
            # Kept, not just carried: the bytes ride this turn's model
            # call and the message remembers where the image was stored,
            # so reopening the conversation still shows it.
            stored_text, stored_metadata = await prepare_turn(message, attachments)
            conversation = self._setup_conversation(
                stored_text,
                conversation_id,
                user_id,
                config=current,
                surface=surface,
                metadata=stored_metadata,
            )

            # Create streaming conversation wrapper
            streaming_conv = StreamingConversation(conversation=conversation)
            streaming_conv.reset_stream()

            # Build health context (always included, refreshed per message)
            health_context, health_warning = await self._build_health_context()

            # Usage and model-catalog self-awareness belong to the
            # default ops assistant; custom agents (a finance analyst,
            # say) would pay their token cost every turn without ever
            # using them.
            is_default_agent = (
                agent_config is None or agent_config.slug == DEFAULT_AGENT_SLUG
            )
            usage_context = self._build_usage_context() if is_default_agent else None
            catalog_context = (
                self._build_catalog_context() if is_default_agent else None
            )

            # Guarded per-user memory block (saved via the save_memory tool)
            memory_context = await build_user_memory_context(user_id)

            # History budget scales with the EFFECTIVE model for this
            # request (the agent's pin wins over the active default).
            from app.services.ai.domains.llm.catalog import context_window_for

            effective_model = (
                agent_config.model_id
                if agent_config is not None and agent_config.model_id
                else current.model
            )
            history_budget = history_char_budget(
                await context_window_for(effective_model)
            )

            # The agent row's memory modules (e.g. the finance snapshot)
            agent_modules_context = await render_memory_modules(
                agent_config.memory_modules, user_id=user_id
            )

            # Prepare agent and conversation context
            agent, conversation_context = self._prepare_agent_and_context(
                conversation,
                health_context=health_context,
                health_warning=health_warning,
                usage_context=usage_context,
                catalog_context=catalog_context,
                agent_config=agent_config,
                memory_context=memory_context,
                agent_modules_context=agent_modules_context,
                history_budget=history_budget,
            )

            # Start streaming
            start_time = datetime.now(UTC)

            # Generate a message ID for the streaming response
            message_id = str(uuid.uuid4())

            # Event-based streaming: unlike ``run_stream`` (which treats the
            # first text output as THE answer and stops), the event stream
            # crosses tool boundaries, so a model that narrates before
            # calling a tool - "let me pull that together" then run_code -
            # streams its preamble, runs the tool, and keeps talking.

            stream_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

            tool_trace: list[dict[str, Any]] = []
            # The turn's user rides a ContextVar for the whole stream:
            # a memory write lands mid-stream, inside a tool call, and
            # has no other way to know whose fact it is.

            with (
                memory_user(
                    user_id,
                    agent_slug=agent_slug,
                    conversation_id=conversation.id,
                ),
                reading_stage() as staged_readings,
            ):
                async with agent.run_stream_events(
                    build_user_content(conversation_context, attachments)
                ) as events:
                    async for event in events:
                        text_chunk = ""
                        if isinstance(event, PartStartEvent) and isinstance(
                            event.part, TextPart
                        ):
                            text_chunk = event.part.content
                        elif isinstance(event, PartDeltaEvent) and isinstance(
                            event.delta, TextPartDelta
                        ):
                            text_chunk = event.delta.content_delta
                        elif isinstance(event, FunctionToolCallEvent):
                            # Text streamed before a tool call is running
                            # commentary ("let me check..."), not the answer.
                            # Reset accumulation so the saved message and final
                            # chunk carry only what follows the last tool call,
                            # and notify the UI which tool is running.
                            streaming_conv.accumulated_content = ""
                            record_tool_call(tool_trace, event)
                            yield self._tool_notice(
                                event.part.tool_name,
                                args=call_args_preview(event.part.args),
                                message_id=message_id,
                                conversation_id=conversation.id,
                                stream_delta=stream_delta,
                            )
                            continue
                        elif isinstance(event, FunctionToolResultEvent):
                            # A completing run_code call carries its sandbox-
                            # dispatched calls as metadata; surface each one so
                            # the UI trail shows what the script actually did.
                            record_tool_result(tool_trace, event)
                            for name, args in nested_tool_calls(event):
                                yield self._tool_notice(
                                    name,
                                    args=args,
                                    message_id=message_id,
                                    conversation_id=conversation.id,
                                    stream_delta=stream_delta,
                                )
                            continue
                        elif isinstance(event, AgentRunResultEvent):
                            usage_obj = (
                                event.result.usage()
                                if callable(getattr(event.result, "usage", None))
                                else getattr(event.result, "usage", None)
                            )
                            if usage_obj:
                                stream_usage = {
                                    "input_tokens": getattr(
                                        usage_obj, "input_tokens", 0
                                    )
                                    or 0,
                                    "output_tokens": getattr(
                                        usage_obj, "output_tokens", 0
                                    )
                                    or 0,
                                }

                        if not text_chunk:
                            continue

                        # Accumulate content
                        total_content = streaming_conv.accumulate_content(
                            text_chunk, is_delta=True
                        )

                        # Yield streaming message chunk
                        yield StreamingMessage(
                            content=text_chunk if stream_delta else total_content,
                            is_final=False,
                            is_delta=stream_delta,
                            message_id=message_id,
                            conversation_id=conversation.id,
                            metadata={
                                "provider": current.provider,
                                "model": current.model,
                                "stream_delta": stream_delta,
                            },
                        )

            end_time = datetime.now(UTC)
            response_time_ms = (end_time - start_time).total_seconds() * 1000

            # Add final message to conversation using accumulated streaming content
            final_content = streaming_conv.accumulated_content or "No content received"
            ai_message = conversation.add_message(
                MessageRole.ASSISTANT, final_content, message_id=message_id
            )

            # Calculate cost for status line
            input_tokens = stream_usage.get("input_tokens", 0)
            output_tokens = stream_usage.get("output_tokens", 0)
            cost = self.calculate_cost(input_tokens, output_tokens)

            # Calculate TPS (tokens per second) for performance metrics
            # This is especially useful for Ollama but works for all providers
            gen_tps: float | None = None
            if output_tokens > 0 and response_time_ms > 0:
                gen_tps = round(output_tokens * 1000 / response_time_ms, 1)

            # One metadata dict: what the final frame reports is what the
            # message keeps, so a conversation reopened from history shows
            # the same model, cost and trace the live turn did. The model
            # is the one that answered: an agent's pin, else the global.
            final_metadata: dict[str, Any] = {
                "conversation_id": conversation.id,
                "provider": current.provider,
                "model": (
                    agent_config.model_id
                    if agent_config is not None and agent_config.model_id
                    else current.model
                ),
                "response_time_ms": response_time_ms,
                "stream_complete": True,
                # Token usage and cost for CLI status line
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost,
                # TPS for performance monitoring (especially useful for Ollama)
                "gen_tps": gen_tps,
            }
            if tool_trace:
                # Persisted with the message, so a conversation reopened
                # from history can still expand what each run did.
                final_metadata["tool_trace"] = tool_trace
            ai_message.metadata.update(final_metadata)

            # Record usage tracking (PydanticAI provides streaming usage)
            self._record_usage(
                f"stream_chat:{agent_config.slug}", stream_usage, user_id
            )

            # Extractions recorded mid-run outlive the image: fold them
            # into the conversation before finalize persists it.
            merge_staged_readings(conversation.metadata, staged_readings)
            # Finalize conversation (update metadata and save)
            self._finalize_conversation(
                conversation, response_time_ms, is_streaming=True
            )

            # Yield final streaming message
            yield StreamingMessage(
                content=final_content,
                is_final=True,
                is_delta=False,
                message_id=message_id,
                conversation_id=conversation.id,
                metadata=final_metadata,
            )

        except (ModelRetry, UnexpectedModelBehavior) as e:
            error_msg = f"AI provider streaming error: {e}"
            logger.error(error_msg)
            raise ProviderError(error_msg) from e

        except ProviderNotInstalledError:
            # Re-raise without wrapping - CLI will handle display
            raise
        except AIServiceError:
            # ConversationError and friends carry meaning for callers (the
            # API router maps them to distinct status codes); wrapping them
            # would collapse that distinction.
            raise
        except Exception as e:
            error_msg = f"Streaming failed: {e}"
            logger.exception(error_msg)
            raise AIServiceError(error_msg) from e
