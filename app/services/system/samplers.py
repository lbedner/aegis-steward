"""What Overseer samples for its live charts (``app.core.series``). Adding
one is an entry here: a name and an async read returning a ``Sample``
(numbers by series name, and the reading whole for the views that show
it); it runs at its ``interval`` while someone watches and its
``idle_interval`` otherwise.
"""

from app.components.inference.sampler import HOST as INFERENCE_HOST
from app.components.inference.sampler import SAMPLER as INFERENCE
from app.services.system import ui_resources, ui_runtime
from app.services.system.redis_keys import KEYSPACE
from app.services.system.ui_worker import QUEUES

SAMPLERS = [
    ui_runtime.CONTAINERS,
    ui_resources.HOST,
    INFERENCE,
    KEYSPACE,
    QUEUES,
]

# Pages whose server can run outside Docker, by the component that owns it
# (``ui_runtime.host_of``).
HOSTS = {
    "inference": INFERENCE_HOST,
}
