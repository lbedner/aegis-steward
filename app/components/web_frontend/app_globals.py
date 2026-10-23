"""The globals and filters steward's own pages read: the sidebar, the
glyphs, and the matters and documents vocabulary. Registered on the one
environment by ``rendering``; the framework's own globals stay there."""

from jinja2 import Environment

from app.components.web_frontend.glyphs import (
    account_glyph,
    category_glyph,
    file_badge,
    file_badge_table,
)
from app.components.web_frontend.nav import NAV
from app.services.documents.models import kind_label
from app.services.finance.constants import account_sections
from app.services.matters.facts import rate_suffix
from app.services.matters.words import (
    fact_attributes,
    item_status,
    item_verb,
    moment_tone,
    role_label,
    word,
)


def register_app_globals(env: Environment) -> None:
    """Put steward's page vocabulary on ``env``."""
    # The sidebar loops this; see nav.py.
    env.globals["nav"] = NAV
    env.globals["category_glyph"] = category_glyph
    env.globals["account_glyph"] = account_glyph
    env.globals["file_badge"] = file_badge
    env.globals["file_badge_table"] = file_badge_table

    # The matters vocabulary: every string those pages say comes from one
    # module, so a status spelled in a template cannot drift from the same
    # status spelled in another.
    env.globals["word"] = word
    env.globals["fact_attributes"] = fact_attributes
    env.filters["role_label"] = role_label
    env.filters["item_status"] = item_status
    env.filters["item_verb"] = item_verb
    env.filters["moment_tone"] = moment_tone
    env.filters["rate_suffix"] = rate_suffix

    # One label for a document's kind, in the tables and the dialog alike.
    env.filters["kind_label"] = kind_label
    env.globals["account_sections"] = account_sections
