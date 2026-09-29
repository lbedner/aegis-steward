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

VENDOR = "openai"
VOICE_MODELS = [m for m in MODELS[VENDOR] if m.get("mode", "chat") != "chat"]


async def seed_voice_catalog(session: AsyncSession) -> None:
    org = (await session.exec(select(LLMOrg).where(LLMOrg.slug == VENDOR))).first()
    if org is None:
        org = LLMOrg(slug=VENDOR, name=VENDOR)
        session.add(org)
        await session.flush()
    assert org.id is not None
    ids = [m["model_id"] for m in VOICE_MODELS]
    have = set(
        (
            await session.exec(
                select(LargeLanguageModel.model_id).where(
                    col(LargeLanguageModel.model_id).in_(ids)
                )
            )
        ).all()
    )
    added = [
        LargeLanguageModel(served_by_org_id=org.id, **m)
        for m in VOICE_MODELS
        if m["model_id"] not in have
    ]
    session.add_all(added)
    await session.flush()
    session.add_all(
        price_row(PRICES[(VENDOR, m.model_id)], org_id=org.id, llm_id=m.id)
        for m in added
        if m.id is not None
    )
    await session.commit()
