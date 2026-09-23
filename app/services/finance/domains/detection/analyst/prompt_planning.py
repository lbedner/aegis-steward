"""The plan's change types in the assistant's words: goals and envelopes.

Spliced into ``PROPOSING_CHANGES`` beside the bills entries, and kept in
a module of its own because that one is at its size budget (#166).
"""

PLANNING_CHANGES = """\
- `goal.create` - payload {"name", "target_cents": int, optional \
"target_date" (YYYY-MM-DD), "monthly_cents": int, "priority": int (lower \
goes first)}: a new goal of its own. `goal.update` - payload \
{"account_id": int (a goal in accounts()), and any of "target_cents", \
"target_date", "monthly_cents", "priority", "status" (active/paused/\
reached)}: correct one - only what you send changes. The forecast takes \
an active goal's monthly amount out on the 1st and a paused one asks \
nothing, so a goal the person has stopped saving for is PAUSED, not \
left at a number the projection keeps drawing down. A goal WITH a \
target date asks what the date needs and ignores "monthly_cents": to \
save $100 a month toward a dated goal, say that the date decides it and \
offer a later date instead - the card's Forecast line shows the real ask.
- `envelope.create` - payload {"name", "allowance_cents": int, \
"cadence": weekly/monthly}; `envelope.update` - payload {"account_id": \
int (an envelope in accounts()), and any of "allowance_cents", \
"cadence", "auto_credit": bool}: an allowance somebody spends down. \
It moves NO money - a $10 weekly envelope credit is a planning \
allocation, not a bank transfer, unless the person says it is one. \
`envelope.balance` - payload {"account_id": int, "balance_cents": int, \
"note": why}: what is really in it, when the person says the record is \
wrong ("she has $10 in"). The weekly credit books on Mondays.
"""
