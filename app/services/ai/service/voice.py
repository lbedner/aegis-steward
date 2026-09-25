"""The voice pipeline: hear the turn, answer it as text would, say the answer.

A spoken turn is a typed turn with a microphone in front of it. The
transcript is the user's message and goes through ``chat`` with the same
agent, surface, user and conversation a typed turn would carry, so tools,
memory, approvals and history are the text path's own. Audio is not kept:
the conversation holds the words, and speech usage is recorded by the STT
and TTS services against the same user.
"""

from app.core.log import logger
from app.services.ai.domains.voice import (
    AudioInput,
    SpeechRequest,
    VoiceChatResponse,
)
from app.services.ai.domains.voice.spoken import to_spoken
from app.services.ai.service.base import AIServiceError
from app.services.ai.service.streaming import StreamingMixin


class VoiceMixin(StreamingMixin):
    """STT -> chat -> TTS on top of the chat mixins."""

    async def voice_chat(
        self,
        audio: AudioInput,
        conversation_id: str | None = None,
        user_id: str = "default",
        agent_slug: str | None = None,
        surface: str | None = None,
        transcription_hint: str | None = None,
        voice_mode: bool = False,
        return_audio: bool = False,
    ) -> VoiceChatResponse:
        """Transcribe ``audio``, run it as a chat turn, optionally speak the answer.

        ``agent_slug`` and ``surface`` are the caller's, exactly as it would
        pass them for a typed turn. ``transcription_hint`` spells names the
        STT model cannot guess. ``voice_mode`` returns the answer as plain
        spoken sentences; ``return_audio`` also synthesizes them.
        """
        if not self.config.enabled:
            raise AIServiceError("AI service is disabled")

        try:
            if transcription_hint:
                audio = audio.model_copy(update={"prompt": transcription_hint})
            transcription = await self.stt.transcribe(audio, user_id=user_id)

            response = await self.chat(
                message=transcription.text,
                conversation_id=conversation_id,
                user_id=user_id,
                agent_slug=agent_slug,
                surface=surface,
            )
            full_response = response.content
            voice_response = (
                await self.prepare_for_voice(full_response)
                if voice_mode or return_audio
                else full_response
            )

            audio_response: bytes | None = None
            if return_audio:
                speech = await self.tts.synthesize(
                    SpeechRequest(text=voice_response), user_id=user_id
                )
                audio_response = speech.audio

            return VoiceChatResponse(
                transcription=transcription,
                full_response=full_response,
                voice_response=voice_response,
                conversation_id=response.metadata.get("conversation_id"),
                audio_response=audio_response,
            )
        except AIServiceError:
            # ProviderError / ConversationError subclasses carry meaning for
            # callers (the API router maps them to distinct status codes).
            raise
        except Exception as e:
            logger.exception("Voice chat processing failed")
            raise AIServiceError("Voice chat processing failed") from e

    async def prepare_for_voice(self, text: str, max_chars: int = 4096) -> str:
        """The answer as it should be said; see ``domains.voice.spoken``."""
        return to_spoken(text, max_chars)
