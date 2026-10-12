"""Holdings, trades, securities, and the investment import.

One sub-router of the finance API (see ``router.py``, the aggregator).
"""

from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.backend.api.finance.base import (
    _INVESTMENT_IMPORT_PROFILES,
    _NOT_FOUND,
    read_upload,
)
from app.components.backend.api.finance.imports import park_import, start_import_job
from app.services.finance.deps import get_finance_service
from app.services.finance.domains.investments.securities import market_value_cents
from app.services.finance.schemas import (
    HoldingCreate,
    HoldingListResponse,
    HoldingResponse,
    SecurityCreate,
    SecurityResponse,
    TradeListResponse,
    TradeResponse,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from app.services.shared.deps import get_owner_user_id

router = APIRouter()


@router.post(
    "/accounts/{account_id}/holdings",
    response_model=HoldingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upsert_holding(
    account_id: int,
    body: HoldingCreate,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> HoldingResponse:
    """Add or update a position (security resolved/created by ticker). Repeating
    an (account, security, date) updates it in place."""
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    security = await service.get_or_create_security(
        ticker=body.ticker, name=body.name, security_type=body.security_type
    )
    holding = await service.upsert_holding(
        owner_user_id=owner_user_id,
        account_id=account_id,
        security_id=security.id,
        as_of_date=body.as_of_date or current_date(),
        quantity_e8=round(body.quantity * 100_000_000),
        price=body.price,
        cost_basis=body.cost_basis,
    )
    price = holding.price if holding.price is not None else security.close_price
    value = market_value_cents(holding.quantity_e8, price, holding.price_scale)
    return HoldingResponse.from_parts(holding, security, value)


@router.get("/accounts/{account_id}/holdings", response_model=HoldingListResponse)
async def list_account_holdings(
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> HoldingListResponse:
    """Current positions in an account, with per-holding + total market value."""
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    holdings = await service.list_current_holdings(
        owner_user_id=owner_user_id, account_id=account_id
    )
    items = [HoldingResponse.from_parts(h, s, v) for h, s, v in holdings]
    await _attach_holding_icons(service.db, items)
    return HoldingListResponse(
        items=items,
        total=len(items),
        portfolio_value=sum(item.market_value for item in items),
    )


def _check_profile(profile: str) -> None:
    """The one ledger layout the job reads; any other is refused before
    the file is stored."""
    if profile not in _INVESTMENT_IMPORT_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown profile {profile!r}. Known: {_INVESTMENT_IMPORT_PROFILES}.",
        )


@router.post("/import-investments/preview", status_code=status.HTTP_202_ACCEPTED)
async def preview_import_investments(
    file: UploadFile = File(...),
    profile: str = "optum",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> dict[str, str]:
    """Parse a custodian activity ledger and report what it carries -
    row count, date range, and the replayed ending positions - without
    touching the database. The dialog between file pick and commit runs
    on this, so the account choice is made looking at real numbers.

    Parsed by the import job (#494): ``202 {"job_id": ...}``, and the
    finished job's result is the ``InvestmentImportPreviewResponse``.
    """
    _check_profile(profile)
    job_id = await start_import_job(
        file,
        account_id=None,
        kind="investments-preview",
        service=service,
        owner_user_id=owner_user_id,
    )
    return {"job_id": job_id}


@router.post("/import-investments", status_code=status.HTTP_202_ACCEPTED)
async def import_investments(
    file: UploadFile = File(...),
    account_id: int | None = None,
    account_name: str | None = None,
    profile: str = "optum",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> dict[str, Any]:
    """Upload a custodian activity ledger (trades/dividends/fees) into a
    brokerage account. The investments counterpart to ``/import``: writes
    ``FinanceTrade``/``FinanceHolding`` rows, not cash transactions.

    Target: an existing ``account_id``, or an ``account_name`` to create a
    manual brokerage account on the spot - the same courtesy the register
    import extends when an OFX file names an account that doesn't exist yet.

    Loaded by the import job (#494): ``202 {"job_id", "account_id",
    "account_created"}``, and the finished job's result is the
    ``InvestmentImportResultResponse``. An account made here is made by
    this request, so this answer is the one that says so.
    """
    _check_profile(profile)
    name = (account_name or "").strip()
    if account_id is None and not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide an account_id, or an account_name to create one.",
        )
    if account_id is not None:
        job_id = await start_import_job(
            file,
            account_id=account_id,
            kind="investments",
            service=service,
            owner_user_id=owner_user_id,
        )
        return {"job_id": job_id, "account_id": account_id, "account_created": False}
    # The size check first: a refused file must not leave an empty account.
    data = await read_upload(file)
    account_id, created = await _brokerage_named(service, name, owner_user_id)
    job_id = await park_import(
        data,
        file,
        account_id=account_id,
        kind="investments",
        owner_user_id=owner_user_id,
    )
    return {"job_id": job_id, "account_id": account_id, "account_created": created}


async def _brokerage_named(
    service: FinanceService, name: str, owner_user_id: int | None
) -> tuple[int, bool]:
    """The caller's live brokerage account called ``name``, made if there
    is none: ``(id, created)``. The account is made before its job runs,
    so a retry after a failed job finds it rather than making a second."""
    from app.services.finance.adapters.importers.queries import (
        live_account_id_by_name,
    )

    existing_id = await live_account_id_by_name(
        service.db, name=name, owner_user_id=owner_user_id, account_type="brokerage"
    )
    if existing_id is not None:
        return existing_id, False
    account = await service.create_manual_account(
        owner_user_id=owner_user_id,
        name=name,
        account_type="brokerage",
        classification="asset",
    )
    # Committed before the job runs: it reads the account in its own session.
    await service.db.commit()
    return account.id, True


@router.get("/accounts/{account_id}/trades", response_model=TradeListResponse)
async def list_account_trades(
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> TradeListResponse:
    """Recent investment activity (buy/sell/dividend/...) for an account,
    newest first."""
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    trades = await service.list_trades(
        owner_user_id=owner_user_id, account_id=account_id
    )
    items = [TradeResponse.from_row(t) for t in trades]
    return TradeListResponse(items=items, total=len(items))


@router.get("/trades", response_model=TradeListResponse)
async def list_all_trades(
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> TradeListResponse:
    """Recent investment activity, newest first — the All Accounts
    register's investment lane. ``account_ids`` is the same account-scope
    filter the register's transaction fetch uses; without it the picker
    narrowed transactions while every brokerage's trades rode along."""
    trades = await service.list_trades(
        owner_user_id=owner_user_id, account_ids=account_ids
    )
    items = [TradeResponse.from_row(t) for t in trades]
    return TradeListResponse(items=items, total=len(items))


@router.get("/holdings", response_model=HoldingListResponse)
async def list_holdings(
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> HoldingListResponse:
    """All current positions across accounts, with total portfolio value."""
    holdings = await service.list_current_holdings(owner_user_id=owner_user_id)
    items = [HoldingResponse.from_parts(h, s, v) for h, s, v in holdings]
    await _attach_holding_icons(service.db, items)
    return HoldingListResponse(
        items=items,
        total=len(items),
        portfolio_value=sum(item.market_value for item in items),
    )


async def _attach_holding_icons(db: AsyncSession, items: list[HoldingResponse]) -> None:
    """Sets ``icon_b64`` from each holding's fund-family logo.

    Same fetch/cache system as payee icons (``merchant_icon.py``) - only
    the domain resolver differs. A fund's own name is never a usable
    domain guess, so unlike payees this ONLY uses the explicit family
    match; an unrecognized family is left ``None`` (initial-letter
    fallback) rather than risking a wrong guessed logo.
    """
    from app.services.finance.domains.ledger.merchant_icon import (
        fund_family_domain,
        icons_for_names,
    )

    domains = {
        item.name: domain
        for item in items
        if item.name and (domain := fund_family_domain(item.name)) is not None
    }
    if not domains:
        return
    icons = await icons_for_names(db, list(domains.keys()), domains_by_name=domains)
    for item in items:
        item.icon_b64 = icons.get(item.name or "")


# -- Investments (securities + holdings) -------------------------------------


@router.post(
    "/securities",
    response_model=SecurityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_security(
    body: SecurityCreate,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> SecurityResponse:
    """Register (or fetch) a catalog security by ticker."""
    security = await service.get_or_create_security(
        ticker=body.ticker,
        name=body.name,
        security_type=body.security_type,
        currency=body.currency,
    )
    return SecurityResponse.from_row(security)
