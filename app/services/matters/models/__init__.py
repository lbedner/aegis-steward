"""The matters models, split at the size budget.

Everything is re-exported here, so ``from app.services.matters.models
import X`` keeps working wherever it already appears - including
alembic's autogenerate, which sees only what is importable.
"""

from app.services.matters.models.core import *  # noqa: F403
from app.services.matters.models.requests import *  # noqa: F403
