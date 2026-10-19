"""A payee's change types in the assistant's words: who a charge was
with, and the payee itself - its name, or two that are one (#328).

Spliced into ``PROPOSING_CHANGES`` where ``transaction.assign_payee``
always sat, and kept in a module of its own because that one is at its
size budget, as the plan's and the case's entries are.
"""

PAYEE_CHANGES = """\
- `transaction.assign_payee` - payload {"transaction_id": int, \
"payee": str}: names who a transaction was really with ("these 12 ATM \
withdrawals are Riverbend Grounded"). The payload takes the payee \
NAME - ledger rows carry their curated 'payee', so reuse an existing \
spelling when one fits; a new name is created once on first approval \
and later assignments reuse it.
- `payee.rename` - payload {"payee": str (its name now, as rows carry \
it), "name": str, optional "website_url"}: a payee's own name is wrong - \
lowercase, the processor's, the wrong brand. Every charge shows the \
payee's name, so this fixes all of them at once; reassigning rows to a \
new spelling would leave two payees for one merchant.
- `payee.merge` - payload {"keep": str, "merge": [str, ...]} (payee \
names): two payees are one merchant ("Shop Rite" and "ShopRite"). The \
merged ones' charges, bills and the descriptors they were taught move to \
the one kept, and they go away. Renaming one to match the other does \
NOT join them.
"""
