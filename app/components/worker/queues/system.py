"""
System worker queue configuration.

Handles system maintenance and monitoring tasks using native arq patterns.
"""

from arq.connections import RedisSettings

from app.components.worker import arq_hooks
from app.components.worker.tasks.chat_tasks import (
    announce_approval_task,
    fold_conversation_task,
)
from app.components.worker.tasks.document_tasks import extract_document_task
from app.components.worker.tasks.finance_tasks import (
    finance_import_task,
    finance_sync_connection_task,
)
from app.components.worker.tasks.mail_tasks import mail_import_task
from app.components.worker.tasks.service_jobs import service_job_tasks
from app.components.worker.tasks.simple_system_tasks import (
    cleanup_temp_files,
    system_health_check,
)
from app.core.config import settings
from app.core.constants import QueueName
from app.core.queue_workers import concurrency_for


async def _start_on_the_picked_model() -> None:
    """Start the job on the model you picked.

    The selection is a database row, switched without a restart, and this
    process never boots through the hook that applies it: a job building
    its model from settings alone ran the .env bootstrap model (#390).
    Read here, before the job opens a session of its own - asked inside
    one, the read waits on that job's write lock.
    """
    from app.services.ai.domains.llm import active_model

    await active_model.sync_from_db(settings)


class WorkerSettings:
    """System maintenance worker configuration."""

    # Human-readable description
    description = "System maintenance and monitoring tasks"

    # Task functions for this queue
    functions = [
        system_health_check,
        cleanup_temp_files,
        announce_approval_task,
        fold_conversation_task,
        extract_document_task,
        finance_import_task,
        finance_sync_connection_task,
        mail_import_task,
        *service_job_tasks(),
    ]

    # arq configuration with improved connection settings
    base_settings = RedisSettings.from_dsn(settings.redis_url_effective)
    redis_settings = RedisSettings(
        host=base_settings.host,
        port=base_settings.port,
        database=base_settings.database,
        password=base_settings.password,
        conn_timeout=settings.REDIS_CONN_TIMEOUT,
        conn_retries=settings.REDIS_CONN_RETRIES,
        conn_retry_delay=settings.REDIS_CONN_RETRY_DELAY,
    )
    queue_name = "arq:queue:system"
    max_jobs = concurrency_for(QueueName.SYSTEM)  # Settings.WORKER_QUEUES
    job_timeout = 300  # 5 minutes
    keep_result = settings.WORKER_KEEP_RESULT_SECONDS
    max_tries = settings.WORKER_MAX_TRIES
    health_check_interval = settings.WORKER_HEALTH_CHECK_INTERVAL

    on_startup, on_shutdown, on_job_start, after_job_end = arq_hooks.for_queue(
        QueueName.SYSTEM, max_jobs, before_job=_start_on_the_picked_model
    )
