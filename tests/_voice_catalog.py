"""The catalog's voice models, for tests that price or dial them.

Seeded from the catalog's own offline seed (``fixtures/llm_catalog``), so
a test prices at exactly the rates a fresh install would. Idempotent: the
app-owned test database lives for the whole run.
"""

from __future__ import annotations

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.fixtures.llm_catalog import MODELS, PRICES
from app.services.ai.fixtures.llm_fixtures import price_row
from app.services.ai.models.llm import LargeLanguageModel, LLMOrg

# Every vendor's voice models, as (vendor, model) pairs.
VOICED = [
    (vendor, model)
    for vendor, models in MODELS.items()
    for model in models
    if model.get("mode", "chat") != "chat"
]
VOICE_MODELS = [model for _, model in VOICED]


async def seed_voice_catalog(session: AsyncSession) -> None:
    """Every vendor's voice models and their prices; one read per table."""
    vendors = list(dict.fromkeys(vendor for vendor, _ in VOICED))
    orgs = {
        org.slug: org
        for org in (
            await session.exec(select(LLMOrg).where(col(LLMOrg.slug).in_(vendors)))
        ).all()
    }
    missing = [
        LLMOrg(slug=vendor, name=vendor) for vendor in vendors if vendor not in orgs
    ]
    session.add_all(missing)
    await session.flush()
    orgs.update({org.slug: org for org in missing})
    have = set(
        (
            await session.exec(
                select(LargeLanguageModel.model_id).where(
                    col(LargeLanguageModel.model_id).in_(
                        [model["model_id"] for model in VOICE_MODELS]
                    )
                )
            )
        ).all()
    )
    added = [
        (vendor, LargeLanguageModel(served_by_org_id=orgs[vendor].id, **model))
        for vendor, model in VOICED
        if model["model_id"] not in have
    ]
    session.add_all(model for _, model in added)
    await session.flush()
    session.add_all(
        price_row(
            PRICES[(vendor, model.model_id)],
            org_id=orgs[vendor].id or 0,
            llm_id=model.id,
        )
        for vendor, model in added
        if model.id is not None
    )
    await session.commit()


# Each catalog model's title, by its id.
TITLES = {m["model_id"]: m["title"] for m in VOICE_MODELS}
