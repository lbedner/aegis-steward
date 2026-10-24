"""AI service facade: what callers outside it import (``app.services.shared.facade``)."""

# ruff: noqa: F401

from typing import TYPE_CHECKING

from app.services.shared import facade

if TYPE_CHECKING:
    from app.services.ai.domains.chat import cards, memory_changes
    from app.services.ai.domains.chat.agent_loader import (
        CODE_MODE_TOOL_CALLS_LIMIT,
        DEFAULT_AGENT_SLUG,
        AgentConfig,
        agent_capabilities,
        resolve_agent,
    )
    from app.services.ai.domains.chat.agent_registry import (
        AgentNotFoundError,
        InvalidAgentUpdateError,
        prompt_history,
        seed_agent,
        update_agent,
    )
    from app.services.ai.domains.chat.attachments import lift_pastes
    from app.services.ai.domains.chat.cards import MARKER, markers_of
    from app.services.ai.domains.chat.fetchers import FetchContext, register_fetcher
    from app.services.ai.domains.chat.memory_modules import (
        list_memory_modules,
        update_memory_module,
    )
    from app.services.ai.domains.chat.pastes import (
        PASTE_THRESHOLD,
        document_text,
        marker,
        read_paste,
        store_document,
        store_paste,
        title_of,
    )
    from app.services.ai.domains.chat.prompts import DEFAULT_SYSTEM_PROMPT
    from app.services.ai.domains.chat.summary import fold_conversation
    from app.services.ai.domains.chat.tools import resolve_tools
    from app.services.ai.domains.chat.user_memory import (
        DEFAULT_MEMORY_USER_ID,
        MEMORY_CATEGORIES,
        current_agent_slug,
        current_conversation_id,
        delete_user_fact,
        load_user_pastes,
        update_user_fact,
    )
    from app.services.ai.domains.llm import active_model
    from app.services.ai.domains.llm import queries as llm_queries
    from app.services.ai.domains.llm.base import ProviderError as ProviderSetupError
    from app.services.ai.domains.llm.config import AIServiceConfig
    from app.services.ai.domains.llm.etl import (
        CatalogStats,
        SyncResult,
        get_catalog_stats,
        sync_llm_catalog,
    )
    from app.services.ai.domains.llm.etl.queries import (
        catalog_is_populated,
        ollama_models_present,
    )
    from app.services.ai.domains.llm.llm_service import (
        clear_active_model,
        get_current_config,
        get_model_info,
        list_modalities,
        list_models,
        list_vendors,
        set_active_model,
    )
    from app.services.ai.domains.llm.picker import (
        display_title,
        filter_models,
        format_context_window,
        format_price,
        group_models,
        is_local_model,
        lab_for_model,
        model_label,
        newest_first,
    )
    from app.services.ai.domains.llm.provider_management import (
        check_provider_dependency_installed,
        get_existing_api_key,
    )
    from app.services.ai.domains.llm.providers import model_for
    from app.services.ai.domains.llm.queries import org_icons, recent_model_ids
    from app.services.ai.domains.llm.writer import write, writer
    from app.services.ai.domains.spend import recording as usage_recording
    from app.services.ai.domains.spend.recording import extract_usage, record_usage
    from app.services.ai.domains.spend.report import usage_report
    from app.services.ai.domains.voice import live_engines, profiles, realtime_calls
    from app.services.ai.domains.voice.models import (
        AudioFormat,
        AudioInput,
        SpeechRequest,
    )
    from app.services.ai.domains.voice.spoken import to_spoken
    from app.services.ai.domains.voice.tts import TTSService
    from app.services.ai.models import AIProvider
    from app.services.ai.models.agents import (
        Agent,
        AgentPromptChange,
        AgentTool,
        MemoryModule,
        Tool,
    )
    from app.services.ai.models.llm import (
        LargeLanguageModel,
        LLMDeployment,
        LLMModality,
        LLMOrg,
        LLMPrice,
    )
    from app.services.ai.models.voice.live_engine import LiveEngine
    from app.services.ai.models.voice.profile import VoiceProfile
    from app.services.ai.service.ai_service import AIService
    from app.services.ai.service.base import (
        AIServiceError,
        ConversationError,
        ProviderError,
    )

__getattr__, __all__ = facade.lazy(__name__, __file__)
