"""The AI service's models, re-exported: its tables (``agents``, ``llm``,
``sentiment``, ``voice``) where it keeps them, its providers
(``providers``) and its in-memory conversations (``conversation``)."""

from .agents import Agent, AgentTool, Tool
from .conversation import (
    AIServiceStatus,
    Conversation,
    ConversationMessage,
    MessageRole,
    StreamingConversation,
    StreamingMessage,
)
from .llm import (
    Direction,
    LargeLanguageModel,
    LLMDeployment,
    LLMModality,
    LLMOrg,
    LLMPrice,
    LLMUsage,
    Modality,
)
from .providers import (
    PROVIDER_CAPABILITIES,
    PROVIDERS,
    AIProvider,
    ProviderCapabilities,
    ProviderConfig,
    get_free_providers,
    get_provider_capabilities,
)
from .sentiment import SentimentAnalysis
from .voice import STTUsage, TTSUsage, VoiceProfile

__all__ = [
    # Agent registry models
    "Agent",
    "AgentTool",
    "Tool",
    # Sentiment analysis
    "SentimentAnalysis",
    # Voice usage (STT / TTS)
    "STTUsage",
    "TTSUsage",
    "VoiceProfile",
    # LLM tracking models
    "LLMOrg",
    "LargeLanguageModel",
    "LLMPrice",
    "LLMModality",
    "Modality",
    "Direction",
    "LLMDeployment",
    "LLMUsage",
    # AI service models
    "AIProvider",
    "MessageRole",
    "ProviderConfig",
    "ConversationMessage",
    "Conversation",
    "StreamingMessage",
    "StreamingConversation",
    "AIServiceStatus",
    "ProviderCapabilities",
    "PROVIDER_CAPABILITIES",
    "PROVIDERS",
    "get_provider_capabilities",
    "get_free_providers",
]
