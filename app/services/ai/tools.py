"""The AI service's tools, for ``app.core.tools.load_tools``: the cards a
chat agent draws, the memory it keeps, what it reads about itself and the
user, and what the user pasted. Each registers where it is defined; this
names them."""

import app.services.ai.domains.chat.cards  # noqa: F401
import app.services.ai.domains.chat.memory_tools  # noqa: F401
import app.services.ai.domains.chat.pastes  # noqa: F401
import app.services.ai.domains.chat.readings  # noqa: F401
import app.services.ai.domains.chat.self_context  # noqa: F401
import app.services.ai.domains.chat.user_memory  # noqa: F401
