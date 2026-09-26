"""Tests for TTS providers."""

from app.services.ai.domains.voice.models import TTSProvider
from app.services.ai.domains.voice.tts import (
    BaseTTSProvider,
    OpenAITTSProvider,
    get_tts_provider,
)


class TestGetTTSProvider:
    """Test get_tts_provider factory function."""

    def test_get_openai_provider(self) -> None:
        """Test factory returns OpenAITTSProvider."""
        provider = get_tts_provider(TTSProvider.OPENAI)

        assert isinstance(provider, OpenAITTSProvider)
        assert provider.provider_type == TTSProvider.OPENAI

    def test_get_provider_with_api_key(self) -> None:
        """Test factory passes API key to provider."""
        provider = get_tts_provider(
            TTSProvider.OPENAI,
            api_key="sk-test-key",
        )

        assert isinstance(provider, OpenAITTSProvider)
        assert provider.api_key == "sk-test-key"

    def test_get_provider_with_model(self) -> None:
        """Test factory passes model to provider."""
        provider = get_tts_provider(
            TTSProvider.OPENAI,
            model="tts-1-hd",
        )

        assert isinstance(provider, OpenAITTSProvider)
        assert provider.model == "tts-1-hd"

    def test_get_provider_with_voice(self) -> None:
        """Test factory passes voice to provider."""
        provider = get_tts_provider(
            TTSProvider.OPENAI,
            voice="nova",
        )

        assert isinstance(provider, OpenAITTSProvider)
        assert provider.default_voice == "nova"

    def test_get_provider_with_kwargs(self) -> None:
        """Test factory passes kwargs to provider."""
        provider = get_tts_provider(
            TTSProvider.OPENAI,
            base_url="https://custom.api.com",
        )

        assert isinstance(provider, OpenAITTSProvider)
        assert provider.base_url == "https://custom.api.com"


class TestOpenAITTSProvider:
    """Test OpenAITTSProvider class."""

    def test_default_model(self) -> None:
        """Test default model is tts-1."""
        provider = OpenAITTSProvider()

        assert provider.model == "tts-1"

    def test_custom_model(self) -> None:
        """Test custom model can be set."""
        provider = OpenAITTSProvider(model="tts-1-hd")

        assert provider.model == "tts-1-hd"

    def test_default_voice(self) -> None:
        """Test default voice is alloy."""
        provider = OpenAITTSProvider()

        assert provider.default_voice == "alloy"

    def test_custom_voice(self) -> None:
        """Test custom voice can be set."""
        provider = OpenAITTSProvider(voice="nova")

        assert provider.default_voice == "nova"

    def test_api_key_stored(self) -> None:
        """Test API key is stored."""
        provider = OpenAITTSProvider(api_key="sk-test")

        assert provider.api_key == "sk-test"

    def test_base_url_stored(self) -> None:
        """Test base URL is stored."""
        provider = OpenAITTSProvider(base_url="https://custom.com")

        assert provider.base_url == "https://custom.com"

    def test_client_lazy_loaded(self) -> None:
        """Test client is not created until needed."""
        provider = OpenAITTSProvider()

        # Client should be None until _get_client is called
        assert provider._client is None

    def test_provider_type_is_openai(self) -> None:
        """Test provider_type is OPENAI."""
        provider = OpenAITTSProvider()

        assert provider.provider_type == TTSProvider.OPENAI

    def test_is_base_tts_provider(self) -> None:
        """Test inherits from BaseTTSProvider."""
        provider = OpenAITTSProvider()

        assert isinstance(provider, BaseTTSProvider)


class TestBaseTTSProviderInterface:
    """Test BaseTTSProvider abstract interface."""

    def test_all_providers_have_synthesize_method(self) -> None:
        """Test all provider classes have synthesize method."""
        providers = [
            OpenAITTSProvider,
        ]

        for provider_class in providers:
            assert hasattr(provider_class, "synthesize")
            # Method should be callable
            provider = provider_class()
            assert callable(provider.synthesize)

    def test_all_providers_have_synthesize_stream_method(self) -> None:
        """Test all provider classes have synthesize_stream method."""
        providers = [
            OpenAITTSProvider,
        ]

        for provider_class in providers:
            assert hasattr(provider_class, "synthesize_stream")
            # Method should be callable
            provider = provider_class()
            assert callable(provider.synthesize_stream)

    def test_all_providers_have_provider_type(self) -> None:
        """Test all provider classes have provider_type class attribute."""
        providers = [
            (OpenAITTSProvider, TTSProvider.OPENAI),
        ]

        for provider_class, expected_type in providers:
            assert hasattr(provider_class, "provider_type")
            assert provider_class.provider_type == expected_type


class TestOpenAIStreamsForReal:
    """``synthesize_stream`` used to await OpenAI's whole response and then
    slice it, so an 843-character answer took 31s before the first byte
    left (2026-09-25). It has to pass the audio on as it is generated."""

    async def test_chunks_are_passed_on_as_they_arrive(self) -> None:
        from types import SimpleNamespace

        from app.services.ai.domains.voice.models import SpeechRequest
        from app.services.ai.domains.voice.tts import OpenAITTSProvider

        arrived: list[bytes] = []
        seen_before_end: list[list[bytes]] = []

        class _Response:
            async def iter_bytes(self, chunk_size: int | None = None):  # noqa: ANN202
                for chunk in (b"ID3", b"frame1", b"frame2"):
                    arrived.append(chunk)
                    yield chunk

        class _Streaming:
            def __init__(self) -> None:
                self.kwargs: dict[str, object] = {}

            def create(self, **kwargs: object) -> _Streaming:
                self.kwargs = kwargs
                return self

            async def __aenter__(self) -> _Response:
                return _Response()

            async def __aexit__(self, *exc: object) -> None:
                return None

        streaming = _Streaming()
        provider = OpenAITTSProvider(model="gpt-4o-mini-tts", api_key="k")
        provider._client = SimpleNamespace(
            audio=SimpleNamespace(
                speech=SimpleNamespace(with_streaming_response=streaming)
            )
        )

        got: list[bytes] = []
        async for chunk in provider.synthesize_stream(
            SpeechRequest(text="Hello there")
        ):
            got.append(chunk)
            seen_before_end.append(list(arrived))

        assert got == [b"ID3", b"frame1", b"frame2"]
        # The first chunk reached us before the rest had been generated.
        assert seen_before_end[0] == [b"ID3"]
        assert streaming.kwargs["input"] == "Hello there"
        assert streaming.kwargs["response_format"] == "mp3"


class TestOpenAIRequestKnobs:
    """``speed`` works on every OpenAI speech model (measured on
    gpt-4o-mini-tts, 2026-09-25: 6.40s -> 4.95s at 1.5); ``instructions``
    (tone, emotion, pacing) exists only on the gpt-4o models."""

    def test_gpt_4o_gets_speed_and_instructions(self) -> None:
        from app.services.ai.domains.voice.models import SpeechRequest
        from app.services.ai.domains.voice.tts import OpenAITTSProvider

        provider = OpenAITTSProvider(model="gpt-4o-mini-tts", api_key="k")
        kwargs = provider._speech_kwargs(
            SpeechRequest(text="Hi", speed=1.4, instructions="Warm.")
        )
        assert kwargs["speed"] == 1.4
        assert kwargs["instructions"] == "Warm."
        assert kwargs["response_format"] == "mp3"

    def test_tts_1_gets_no_instructions(self) -> None:
        from app.services.ai.domains.voice.models import SpeechRequest
        from app.services.ai.domains.voice.tts import OpenAITTSProvider

        provider = OpenAITTSProvider(model="tts-1", api_key="k")
        kwargs = provider._speech_kwargs(SpeechRequest(text="Hi", instructions="Warm."))
        assert "instructions" not in kwargs
        assert kwargs["speed"] == 1.0  # unset means the model's normal pace
