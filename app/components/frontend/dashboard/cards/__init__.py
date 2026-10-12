"""Dashboard component cards."""

from app.components.frontend.dashboard.cards.ai_card import AICard
from app.components.frontend.dashboard.cards.auth_card import AuthCard
from app.components.frontend.dashboard.cards.comms_card import CommsCard
from app.components.frontend.dashboard.cards.documents_card import DocumentsCard
from app.components.frontend.dashboard.cards.finance_card import FinanceCard

from .database_card import DatabaseCard
from .ingress_card import IngressCard
from .ollama_card import OllamaCard
from .redis_card import RedisCard
from .scheduler_card import SchedulerCard
from .server_card import ServerCard
from .services_card import ServicesCard
from .worker_card import WorkerCard

__all__ = [
    "ServerCard",
    "AICard",
    "AuthCard",
    "CommsCard",
    "DocumentsCard",
    "FinanceCard",
    "ServicesCard",
    "DatabaseCard",
    "IngressCard",
    "OllamaCard",
    "RedisCard",
    "SchedulerCard",
    "WorkerCard",
]
