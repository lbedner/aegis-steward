"""
Dashboard Modal Components

Reusable modal dialogs for displaying detailed component information.
Each modal inherits from ft.AlertDialog and uses component composition.
"""

from app.components.frontend.dashboard.modals.ai_modal import AIDetailDialog
from app.components.frontend.dashboard.modals.auth_modal import AuthDetailDialog
from app.components.frontend.dashboard.modals.comms_modal import CommsDetailDialog
from app.components.frontend.dashboard.modals.documents_modal import (
    DocumentsDetailDialog,
)
from app.components.frontend.dashboard.modals.finance_modal import FinanceDetailDialog

from .backend_modal import BackendDetailDialog
from .database_modal import DatabaseDetailDialog
from .frontend_modal import FrontendDetailDialog
from .ingress_modal import IngressDetailDialog
from .redis_modal import RedisDetailDialog
from .scheduler_modal import SchedulerDetailDialog
from .secrets_modal import SecretsDetailDialog
from .worker_modal import WorkerDetailDialog

__all__ = [
    "AIDetailDialog",
    "AuthDetailDialog",
    "CommsDetailDialog",
    "DocumentsDetailDialog",
    "FinanceDetailDialog",
    "BackendDetailDialog",
    "DatabaseDetailDialog",
    "FrontendDetailDialog",
    "IngressDetailDialog",
    "SecretsDetailDialog",
    "RedisDetailDialog",
    "SchedulerDetailDialog",
    "WorkerDetailDialog",
]
