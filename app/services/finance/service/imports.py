"""Import batches and the rows they wrote.

One mixin of the ``FinanceService`` facade: every method here forwards
to the matching domain module as ``module.func(self.db, ...)``.
"""

from __future__ import annotations

from app.services.finance.adapters.importers import imports
from app.services.finance.models import (
    FinanceImportBatch,
    FinanceImportBatchRow,
)
from app.services.finance.service.base import FinanceServiceBase
from app.services.system.jobs import SetLabel


class ImportsMixin(FinanceServiceBase):
    """Import batches and the rows they wrote."""

    async def get_import_batch(
        self, batch_id: int, *, owner_user_id: int | None = None
    ) -> FinanceImportBatch | None:
        # finance_import_batch.owner_user_id is NOT NULL; standalone uses 0.
        return await imports.get_import_batch(
            self.db,
            batch_id,
            owner_user_id=owner_user_id,
        )

    async def list_import_batches(
        self,
        *,
        owner_user_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[FinanceImportBatch]:
        return await imports.list_import_batches(
            self.db,
            owner_user_id=owner_user_id,
            page=page,
            page_size=page_size,
        )

    async def list_import_batch_rows(
        self, batch_id: int
    ) -> list[FinanceImportBatchRow]:
        return await imports.list_import_batch_rows(self.db, batch_id)

    async def preview_file(
        self,
        *,
        owner_user_id: int | None,
        file_name: str | None,
        file_bytes: bytes,
        account_id: int | None = None,
    ) -> imports.ImportPlan:
        return await imports.preview_file(
            self.db,
            owner_user_id=owner_user_id,
            file_name=file_name,
            file_bytes=file_bytes,
            account_id=account_id,
        )

    async def import_file(
        self,
        *,
        owner_user_id: int | None,
        file_name: str | None,
        file_bytes: bytes,
        account_id: int | None = None,
        on_label: SetLabel | None = None,
    ) -> imports.ImportResult:
        from app.services.finance.domains.planning.envelope_tags import settle

        result = await imports.import_file(
            self.db,
            owner_user_id=owner_user_id,
            file_name=file_name,
            file_bytes=file_bytes,
            account_id=account_id,
            on_label=on_label,
        )
        # Tags arrive with a Quicken import, so an envelope that pays for
        # one may owe for what just came in (#240). After the import's own
        # transfer pass, so a tagged transfer is already known as one.
        # ponytail: the CLI and demo seed call the adapter directly and
        # skip this; the next tag change or import settles them.
        await settle(self.db, owner_user_id=owner_user_id)
        return result
