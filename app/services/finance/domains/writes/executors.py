"""The registered change types - one ``register()`` entry per type.

FW-05 shipped categorize to prove the loop end to end; later FW tickets
add theirs HERE. Definitions live in the topic modules (``curation`` for
the label axes, ``structure`` for what a row is, ``terms`` for what a
debt costs); importing this module arms the whole surface.
"""

from __future__ import annotations

from app.services.finance.domains.writes import accounts, curation, structure, terms
from app.services.finance.domains.writes.registry import ChangeExecutor, register
from app.services.matters import changes as matters

register(
    ChangeExecutor(
        change_type="transaction.categorize",
        title="Categorize a transaction",
        payload_model=curation.CategorizePayload,
        execute=curation.categorize_execute,
        describe=curation.categorize_describe,
    )
)
register(
    ChangeExecutor(
        change_type="transaction.memo",
        title="Note what a transaction was",
        payload_model=curation.MemoPayload,
        execute=curation.memo_execute,
        describe=curation.memo_describe,
    )
)
register(
    ChangeExecutor(
        change_type="transaction.assign_payee",
        title="Assign a payee",
        payload_model=curation.AssignPayeePayload,
        execute=curation.assign_payee_execute,
        describe=curation.assign_payee_describe,
    )
)
register(
    ChangeExecutor(
        change_type="account.create",
        title="Add an account the ledger cannot see",
        payload_model=accounts.CreateAccountPayload,
        execute=accounts.create_account_execute,
        describe=accounts.create_account_describe,
    )
)
register(
    ChangeExecutor(
        change_type="account.valuation",
        title="Record what an asset was worth",
        payload_model=terms.ValuationPayload,
        execute=terms.valuation_execute,
        describe=terms.valuation_describe,
    )
)
register(
    ChangeExecutor(
        change_type="document.file",
        title="File a document against an account",
        payload_model=terms.FileDocumentPayload,
        execute=terms.file_document_execute,
        describe=terms.file_document_describe,
    )
)
register(
    ChangeExecutor(
        change_type="account.loan_terms",
        title="Record a loan's terms",
        payload_model=terms.LoanTermsPayload,
        execute=terms.loan_terms_execute,
        describe=terms.loan_terms_describe,
    )
)
register(
    ChangeExecutor(
        change_type="recurring.match",
        title="Match a payment to a bill",
        payload_model=structure.MatchPayload,
        execute=structure.match_execute,
        describe=structure.match_describe,
    )
)
register(
    ChangeExecutor(
        change_type="recurring.declare",
        title="Declare a bill or an income",
        payload_model=structure.DeclarePayload,
        execute=structure.declare_execute,
        describe=structure.declare_describe,
    )
)
register(
    ChangeExecutor(
        change_type="transaction.tag",
        title="Tag a transaction",
        payload_model=curation.TagPayload,
        execute=curation.tag_execute,
        describe=curation.tag_describe,
    )
)
register(
    ChangeExecutor(
        change_type="transaction.untag",
        title="Remove a tag from a transaction",
        payload_model=curation.TagPayload,
        execute=curation.untag_execute,
        describe=curation.untag_describe,
    )
)
register(
    ChangeExecutor(
        change_type="transaction.split",
        title="Split a transaction",
        payload_model=structure.SplitChangePayload,
        execute=structure.split_execute,
        describe=structure.split_describe,
    )
)

# The first change type from outside finance. Working a matter adds
# types, not tools - which is why it registers here rather than growing
# a second approval queue nobody would see.
register(
    ChangeExecutor(
        change_type="fact.record",
        title="Record what can be said about someone's money",
        payload_model=matters.RecordFactPayload,
        execute=matters.record_fact_execute,
        describe=matters.record_fact_describe,
    )
)
register(
    ChangeExecutor(
        change_type="ask.amend",
        title="Correct an ask",
        payload_model=matters.AmendAskPayload,
        execute=matters.amend_ask_execute,
        describe=matters.amend_ask_describe,
    )
)
register(
    ChangeExecutor(
        change_type="ask.add",
        title="Add an ask the letter makes",
        payload_model=matters.AddAskPayload,
        execute=matters.add_ask_execute,
        describe=matters.add_ask_describe,
    )
)
