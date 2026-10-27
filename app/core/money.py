"""Money as people read it: minor units to ``-$1,234.56``, and the
dollars a model is shown beside the cents it computes with."""

from typing import Any

from pydantic import BaseModel

from app.core.tools import result_data

ZERO_DECIMAL_CURRENCIES = {"JPY", "KRW"}
# Symbols for the codes a household ledger actually sees; anything else
# shows its code.
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def cents_named(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    """``data`` with each of ``keys`` renamed to ``<key>_cents``: a shape
    built elsewhere, named for a model that must not guess its unit (#291).
    """
    return {f"{k}_cents" if k in keys else k: v for k, v in data.items()}


def with_dollars(value: Any) -> Any:
    """``value`` with every ``*_cents`` whole number joined by its dollars
    as ``*_usd`` (``-149203`` -> ``"-$1,492.03"``), all the way down.

    What a model reads (#291): tools keep cents - scripts, cards and
    proposals compute with them - and a model left to convert read an
    unlabelled 297614 as dollars and put -149203 at -$149. The cents stay
    so a later script can reuse the exact figure. Anything else, printed
    text included, passes through. A tool that hands back a model is read
    as its JSON data, so its cents are joined the same way."""
    if isinstance(value, BaseModel):
        value = result_data(value)
    if isinstance(value, list):
        return [with_dollars(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[Any, Any] = {}
    for key, item in value.items():
        out[key] = with_dollars(item)
        if (
            isinstance(key, str)
            and key.endswith("_cents")
            and isinstance(item, int)
            and not isinstance(item, bool)
        ):
            out[f"{key.removesuffix('_cents')}_usd"] = format_money(item)
    return out


def format_money(
    cents: int | None, currency: str = "USD", whole: bool = False, signed: bool = False
) -> str:
    """Minor units -> ``-$1,234.56`` (the Flet register's ``_usd`` rule,
    widened to honour the currency code).

    ``whole`` rounds the cents away for somewhere they are noise rather
    than precision - a month chip reading ``Nov $4,208`` where the
    figure is a projection, not a statement. It is the only reason to
    format money any other way, which is why it lives here instead of in
    the f-string that wanted it. ``signed`` marks money arriving with a
    ``+``, for a figure whose sign is the point.
    """
    code = (currency or "USD").upper()
    if code in ZERO_DECIMAL_CURRENCIES:
        value, number = cents or 0, f"{abs(cents or 0):,}"
    elif whole:
        value = (cents or 0) / 100
        number = f"{abs(round(value)):,}"
    else:
        value = (cents or 0) / 100
        number = f"{abs(value):,.2f}"
    sign = "-" if value < 0 else "+" if signed and value > 0 else ""
    symbol = CURRENCY_SYMBOLS.get(code)
    return f"{sign}{symbol}{number}" if symbol else f"{sign}{code} {number}"
