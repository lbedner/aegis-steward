"""The payee memory: what a bank descriptor was taught to mean.

Naming a payee used to be a one-time stamp - ``assign_merchant`` set
merchant_id on the rows in front of you and nothing recorded what the
DESCRIPTOR meant, so the next import offered the same groups again. These
are the other half: written as payees are named, read when an import
creates a transaction.

Its own module rather than more of ``merchants``: that one is the payee
directory (CRUD, merge, the naming backlog), this is the memory of what
naming decided, and they are separately readable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import Protocol

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries
from app.services.finance.models import (
    FinanceMerchantAlias,
    FinanceTransaction,
)
from app.services.finance.utils import (
    normalize_payee,
    transaction_payee_key,
    utcnow,
)

# A payee's usual category files a row nobody filed only when its filed
# rows agree. Measured on the live ledger (#324, September's pairs):
# 90% of 3+ rows filed 102 right and 7 wrong; the plain most-common,
# 109 right and 23 wrong - Target, Dollar General and Amazon split.
SETTLED_SHARE = 0.9
SETTLED_ROWS = 3


class Worded(Protocol):
    """A row as the memory reads it: a stored, a synced or an imported one."""

    original_description: str | None
    name: str | None


def wording(row: Worded) -> str | None:
    """What a row says, as the memory reads it: its descriptor, else its
    name."""
    return row.original_description or row.name


def _wanted(
    taught: Iterable[tuple[str, int, str]],
) -> dict[str, tuple[int, str, bool]]:
    """``(key, payee, sample)`` lessons -> ``key -> (payee, sample,
    ambiguous)``: the most common payee, flagged when there are two."""
    tallies: dict[str, Counter[int]] = defaultdict(Counter)
    samples: dict[str, str] = {}
    for key, merchant_id, sample in taught:
        if key:
            tallies[key][merchant_id] += 1
            samples.setdefault(key, sample)
    return {
        key: (tally.most_common(1)[0][0], samples[key], len(tally) > 1)
        for key, tally in tallies.items()
    }


def _from_pairs(
    pairs: Iterable[tuple[FinanceTransaction, int]],
) -> list[tuple[str, int, str]]:
    """What each bank row an export's row stands for teaches: its wording
    means the export row's payee (#324). Keyed as a synced row is
    resolved (``resolve_merchant_aliases`` on its ``wording``)."""
    return [
        (
            transaction_payee_key(None, None, said),
            merchant_id,
            said,
        )
        for bank, merchant_id in pairs
        if (said := wording(bank))
    ]


async def _write_payee_aliases(
    db: AsyncSession,
    wanted: dict[str, tuple[int, str, bool]],
    *,
    owner_user_id: int | None = None,
) -> int:
    """Upsert ``key -> (merchant_id, sample, ambiguous)``, returning how
    many keys ended up flagged.

    The one place an alias row is written, so the two callers - naming a
    payee, and rebuilding the whole memory from what is already named -
    cannot drift on what a conflict means. A key is flagged either
    because the caller already knows the history holds two payees, or
    because the row on disk points somewhere else, which is that same
    conflict arriving one naming at a time.
    """
    if not wanted:
        return 0
    existing = await queries.merchant_aliases_by_normalized(
        db, wanted, owner_user_id=owner_user_id
    )
    flagged = 0
    for key, (merchant_id, sample, ambiguous) in wanted.items():
        alias = existing.get(key)
        ambiguous = ambiguous or (
            alias is not None and alias.merchant_id != merchant_id
        )
        flagged += int(ambiguous)
        if alias is None:
            db.add(
                FinanceMerchantAlias(
                    owner_user_id=owner_user_id,
                    merchant_id=merchant_id,
                    alias_text=sample,
                    normalized_alias=key,
                    is_ambiguous=ambiguous,
                    source="user",
                )
            )
        elif alias.merchant_id != merchant_id or alias.is_ambiguous != ambiguous:
            alias.merchant_id = merchant_id
            alias.alias_text = sample
            alias.is_ambiguous = ambiguous
            alias.updated_at = utcnow()
            db.add(alias)
    await db.flush()
    return flagged


def _payee_key_samples(
    rows: Sequence[FinanceTransaction],
) -> dict[str, str]:
    """The distinct payee keys in these rows, each with a descriptor to
    show for it."""

    samples: dict[str, str] = {}
    for txn in rows:
        key = transaction_payee_key(
            txn.merchant_name, txn.original_description, txn.name
        )
        if key:
            samples.setdefault(
                key, txn.merchant_name or txn.original_description or txn.name or ""
            )
    return samples


async def remember_payee_keys(
    db: AsyncSession,
    rows: Sequence[FinanceTransaction],
    merchant_id: int,
    *,
    owner_user_id: int | None = None,
) -> None:
    """Record what each payee key being named MEANS, so the next import
    does not ask again.

    Keyed on ``transaction_payee_key`` - the same four-token grouping the
    user was shown when they named it, which is the point: they confirmed
    a GROUP, so the group is what was taught. Keying on the whole
    descriptor instead was measured against this ledger and is close to
    useless as a memory: ShopRite's 407 rows carry 211 distinct
    descriptors and only 8 prefixes.

    A key taught a SECOND payee is not a correction to apply silently -
    it is a conflict ("NON CHASE ATM WITHDRAW" covers four real payees
    here). The newest answer is kept, because the user just gave it, but
    the key is flagged and stops resolving unattended from then on.
    """
    await _write_payee_aliases(
        db,
        {
            key: (merchant_id, sample, False)
            for key, sample in _payee_key_samples(rows).items()
        },
        owner_user_id=owner_user_id,
    )


async def recompute_payee_aliases(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> dict[str, int]:
    """Rebuild the payee memory from the transactions already named.

    The alias table is written as payees are named, so it starts empty on
    a ledger whose naming happened before it existed - 11,189 named
    transactions here that would otherwise teach it nothing. This reads
    those namings back.

    Deliberately a recompute rather than a one-off backfill, and it
    answers to the transactions rather than to what it wrote last time:
    running it twice changes nothing, and a payee since cleared drops its
    key instead of leaving the ledger believing an answer the data no
    longer supports. Two things keep bringing the empty-table state back.
    ``finance restore`` loads archives written before this table existed,
    and every alias is DERIVED from ``transaction_payee_key``, so a
    change to that key's shape leaves every stored row stale with nothing
    to notice.

    A key that several payees share is flagged from the HISTORY rather
    than waiting to be taught twice, because on a real ledger the
    conflict is already sitting in the data: "NON CHASE ATM WITHDRAW"
    covers four payees here before anybody names anything.
    """

    rows = await queries.named_transactions(db, owner_user_id=owner_user_id)
    samples = _payee_key_samples(rows)
    named = [
        (key, txn.merchant_id, samples[key])
        for txn in rows
        if txn.merchant_id is not None
        and (
            key := transaction_payee_key(
                txn.merchant_name, txn.original_description, txn.name
            )
        )
    ]
    # And what the bank's wording was taught by the charges both feeds
    # brought: a rebuild that forgot it would leave synced rows unnamed.
    pairs = await queries.paired_payees(db, owner_user_id=owner_user_id)

    await queries.delete_merchant_aliases(db, owner_user_id=owner_user_id)
    wanted = _wanted(named + _from_pairs(pairs))
    flagged = await _write_payee_aliases(db, wanted, owner_user_id=owner_user_id)
    return {
        "transactions": len(rows),
        "keys": len(wanted),
        "ambiguous": flagged,
    }


async def resolve_merchant_aliases(
    db: AsyncSession,
    descriptors: Iterable[str | None],
    *,
    owner_user_id: int | None = None,
) -> dict[str, int]:
    """descriptor -> payee id for the ones already named unambiguously,
    one query for the whole batch.

    Unmatched descriptors are ABSENT rather than mapped to None, so a
    caller cannot mistake "never taught" for "taught to be nothing". A
    key flagged ambiguous is treated as never taught: the ledger has
    been shown it means two different payees, and naming by hand is
    better than a confident wrong answer.
    """

    by_descriptor = {
        text: transaction_payee_key(None, None, text) for text in descriptors if text
    }
    rows = await queries.merchant_aliases_by_normalized(
        db, by_descriptor.values(), owner_user_id=owner_user_id
    )
    return {
        text: rows[key].merchant_id
        for text, key in by_descriptor.items()
        if key in rows and not rows[key].is_ambiguous
    }


async def learn_from_pairs(
    db: AsyncSession, pairs: Iterable[tuple[FinanceTransaction, FinanceTransaction]]
) -> None:
    """Teach the memory the bank's wording from each ``(export row, bank
    row)`` just paired, where the export's row has a payee (#324): the
    same charge, so the wording means that payee."""
    by_owner: dict[int | None, list[tuple[FinanceTransaction, int]]] = defaultdict(list)
    for export, bank in pairs:
        if export.merchant_id is not None:
            by_owner[bank.owner_user_id].append((bank, export.merchant_id))
    for owner_user_id, taught in by_owner.items():
        await _write_payee_aliases(
            db, _wanted(_from_pairs(taught)), owner_user_id=owner_user_id
        )


async def payees_for(
    db: AsyncSession,
    rows: Iterable[tuple[str | None, str | None]],
    *,
    owner_user_id: int | None = None,
) -> dict[tuple[str | None, str | None], int]:
    """``(wording, provider's name)`` -> payee, for synced rows (#324):
    what the memory was taught the wording means, else the payee the
    provider's own name for it is - when you have one called that. Absent
    when neither says. Two reads for the batch."""
    rows = set(rows)
    taught = await resolve_merchant_aliases(
        db, [said for said, _name in rows], owner_user_id=owner_user_id
    )
    called = await queries.merchants_by_normalized_names(
        db,
        [normalize_payee(name or "") for _said, name in rows],
        owner_user_id=owner_user_id,
    )
    found: dict[tuple[str | None, str | None], int] = {}
    for said, name in rows:
        payee = taught.get(said or "")
        if payee is None and (by_name := called.get(normalize_payee(name or ""))):
            payee = by_name.id
        if payee is not None:
            found[(said, name)] = payee
    return found


async def filed_under(db: AsyncSession, ids: Iterable[int]) -> dict[int, int]:
    """Where each payee files a row nobody has filed: its default, else
    the category nearly all of its filed rows share (``SETTLED_SHARE`` of
    ``SETTLED_ROWS`` or more). Absent when neither: a guess is worse than
    asking. Two reads for the batch."""
    wanted = set(ids)
    if not wanted:
        return {}
    defaults = {
        merchant_id: merchant.default_category_id
        for merchant_id, merchant in (
            await queries.merchants_by_ids(db, wanted)
        ).items()
        if merchant.default_category_id is not None
    }
    tallies: dict[int, Counter[int]] = defaultdict(Counter)
    for merchant_id, category_id, count in await queries.category_tallies_by_merchants(
        db, wanted - defaults.keys()
    ):
        if category_id is not None:
            tallies[merchant_id][category_id] += count
    settled = {}
    for merchant_id, tally in tallies.items():
        category_id, count = tally.most_common(1)[0]
        filed = tally.total()
        if filed >= SETTLED_ROWS and count / filed >= SETTLED_SHARE:
            settled[merchant_id] = category_id
    return settled | defaults
