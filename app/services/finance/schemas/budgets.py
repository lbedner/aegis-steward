"""Budget, envelope, and month-verdict shapes.

A topic module of the ``schemas`` package; every name here is
re-exported from the package root, which stays the one import path.
Money fields are integer minor units (cents); the frontend formats them.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, computed_field, field_validator

from app.core.schema import known
from app.services.finance.constants import ENVELOPE_CADENCES


def _real_month(value: int) -> int:
    if not (100_001 <= value <= 999_912 and 1 <= value % 100 <= 12):
        raise ValueError("month is YYYYMM.")
    return value


# A budget month, YYYYMM. One check for the API and the cards.
PeriodMonth = Annotated[int, AfterValidator(_real_month)]

# What a month allows one line, in cents. More than zero: taking a limit
# off is a removal, not a $0 limit.
LimitCents = Annotated[int, Field(gt=0)]


def line_label(category_name: str | None, payee_label: str | None) -> str:
    """What every surface calls a budget line. A one-off's own name comes
    before its category; a limit on neither target is the overall limit."""
    return payee_label or category_name or "Overall"


class BudgetMonthOutlook(BaseModel):
    """One future month's header equation, bills at face value on their
    real cadence - the month the annual premium lands looks like itself."""

    period_month: int  # YYYYMM
    income_due: int
    bills_due: int
    budgets: int
    goals: int
    envelopes: int
    everything_else: int
    month_net: int
    # The level under the rates: cash compounded from today's balance.
    start_balance: int = 0
    end_balance: int = 0


class BudgetMonthActuals(BaseModel):
    """What came in and went out in one month, money moved between your
    own accounts aside: a month that has ended, as it happened."""

    period_month: int  # YYYYMM
    money_in: int
    money_out: int


class BudgetOutlookResponse(BaseModel):
    items: list[BudgetMonthOutlook]
    total: int


class EnvelopeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    monthly_credit: int | None = Field(default=None, ge=0)
    cadence: str = "monthly"
    _known_cadence = field_validator("cadence")(known(tuple(ENVELOPE_CADENCES)))
    starting_balance: int = Field(default=0, ge=0)


class EnvelopeUpdate(BaseModel):
    """Whole-state update: both fields, every time (an envelope has two)."""

    monthly_credit: int | None = Field(default=None, ge=0)
    auto_credit: bool = False
    cadence: str = "monthly"
    _known_cadence = field_validator("cadence")(known(tuple(ENVELOPE_CADENCES)))
    # The tag whose charges it pays for (#240): None leaves it as it is,
    # "" stops following one.
    tag: str | None = None
    # When it starts counting that tag's charges; None leaves it.
    tag_since: date | None = None


class EnvelopeMove(BaseModel):
    """A credit or a spend - always positive; the endpoint carries the sign."""

    amount: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=255)
    when: date | None = None


class EnvelopeResponse(BaseModel):
    account_id: int
    name: str
    balance: int  # cents; may be negative (borrowed against next month)
    monthly_credit: int | None
    auto_credit: bool
    cadence: str
    # How it fills, worded once for both frontends:
    # "+$20.00/wk automatically", or "topped up by hand".
    credit_caption: str
    # What it pays for: charges wearing this tag, from this date (#240).
    tag: str | None = None
    tag_since: date | None = None


class EnvelopeListResponse(BaseModel):
    items: list[EnvelopeResponse]
    total: int


class BudgetSuggestion(BaseModel):
    """A budget line your own spending already implies."""

    category_id: int
    category_name: str | None = None
    suggested_amount: int  # cents/month, the MEDIAN of complete months
    months_seen: int
    # Months out of the six that did not look like the others (outside
    # +/-50% of the median). 0 is a category that never varies; more than
    # one and it is not suggested at all.
    unusual_months: int
    # Those two counts as the row says them, worded once for both
    # frontends: "6 of 6 months · 1 month stood out".
    evidence: str


class DismissedBudgetSuggestion(BaseModel):
    """A suggestion the user declined - excluded until restored."""

    category_id: int
    category_name: str | None = None


class BudgetSuggestionListResponse(BaseModel):
    items: list[BudgetSuggestion]
    total: int
    # The complete months the picks were read from.
    lookback_months: int
    dismissed: list[DismissedBudgetSuggestion] = Field(default_factory=list)


class BudgetSuggestionIds(BaseModel):
    """Request body for dismissing or restoring suggestions."""

    category_ids: list[int]


class BudgetLineUpsert(BaseModel):
    """POST body for /budget/lines - exactly one of category_id/payee_key."""

    category_id: int | None = None
    payee_key: str | None = None
    payee_label: str | None = None
    allocated_amount: LimitCents
    # Leftover and overspending carry into the next month (#360). None
    # leaves the limit as it was: only the row's checkbox and Illiana's
    # card say rollover, not every form that sets an amount.
    rollover_enabled: bool | None = None


class BudgetLineResponse(BaseModel):
    """A Flexible line is a chosen limit: ``status`` reads spend against
    ``allocated_amount``. A Fixed/Non-monthly line is a detected bill
    shown for context, not a limit anyone set - ``allocated_amount`` is
    just what it typically costs, ``status`` reads ``variance_amount``
    (this period's actual vs. last period's) instead, and never goes
    ``critical`` - a bill can't be "over budget" on itself."""

    id: int
    category_id: int | None
    category_name: str | None
    payee_key: str | None
    payee_label: str | None
    allocated_amount: int
    spent_amount: int
    status: Literal["good", "warn", "critical"]
    # Fixed/Non-monthly only: this period's actual vs. last period's
    # (signed cents); None for Flexible lines and for a bill with no
    # prior-period data yet.
    variance_amount: int | None = None
    # One-time only: the day the plan lands. A one-off renders at face
    # value beside its date, never as a "/mo" figure.
    due_date: date | None = None
    # A limit that rolls over (#360), and what it carries in: everything
    # earlier months allowed and did not spend, less what they overspent.
    rollover: bool = False
    carried_amount: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def available_amount(self) -> int:
        """What the month can spend against the line: its limit plus what
        it carried in."""
        return self.allocated_amount + self.carried_amount

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        """See ``line_label``. A bill always arrives with a category name,
        so it never reads as the overall limit."""
        return line_label(self.category_name, self.payee_label)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spent_ratio(self) -> float:
        """Spend against what is available. Nothing available and
        something spent is wholly over, the way ``budget_line_status``
        judges it."""
        if self.available_amount > 0:
            return self.spent_amount / self.available_amount
        return 1.0 if self.spent_amount > 0 else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spent_percent(self) -> int:
        """The percent every surface shows. Truncated, never rounded: the
        tone flips at exactly 80%, and rounding 79.96% up to "80%" showed
        a figure that had reached the threshold beside a bar that had not."""
        return int(self.spent_ratio * 100)


class BudgetBucketResponse(BaseModel):
    """One of the Budget-tab sections."""

    name: Literal["fixed", "non_monthly", "one_time", "flexible"]
    total_allocated: int
    total_spent: int
    lines: list[BudgetLineResponse]


class BudgetStatsResponse(BaseModel):
    """The Budget tab's 4-cell summary strip."""

    flexible_spent: int
    flexible_allocated: int
    days_left_in_period: int
    flexible_count: int
    on_track_count: int
    over_budget_count: int
    over_budget_labels: list[str]
    fixed_total: int
    fixed_count: int
    # The month's bottom line: confirmed income minus confirmed bills
    # minus budget allocations, monthly-equivalent throughout - the same
    # gate and factors the forecast uses, so the two cannot disagree.
    income_total: int = 0
    income_count: int = 0
    # Active goals' evaluated monthly ask - month_net subtracts it, and
    # the Budgets cell captions it.
    goals_total: int = 0
    goals_count: int = 0
    # What the goals ask for and the month does not have. Only fixed and
    # percent rules can produce one; a surplus sweep cannot overspend.
    goals_shortfall: int = 0
    envelopes_total: int = 0
    envelopes_count: int = 0
    # Observed spending no bill and no limit covers (trailing 3-month
    # average) - the term that keeps the verdict honest.
    everything_else: int = 0
    # Uncovered spending seen in only ONE month of the window: a total,
    # not a rate. Separate because averaging a one-off is what made a
    # single car repair read as a monthly habit for three months.
    one_off_total: int = 0
    month_net: int = 0
    # Deficit left over even after trimming every budget to its floor -
    # the part of a negative month that belongs to bills or income.
    trim_residual: int = 0


class BudgetTrimResponse(BaseModel):
    """One row of the close-the-gap plan, in either kind.

    ``pause_goal`` rows carry account_id/recovered (pausing recovers the
    goal's whole ask); ``cut_budget`` rows carry the line fields - the
    floor is what the line already spent this period, and ``suggested``
    never goes below it. Applying one is a status PATCH or the ordinary
    line upsert respectively."""

    kind: Literal["pause_goal", "cut_budget"] = "cut_budget"
    label: str
    # cut_budget fields
    id: int | None = None
    category_id: int | None = None
    payee_key: str | None = None
    allocated_amount: int | None = None
    spent_amount: int | None = None
    cut: int | None = None
    suggested_amount: int | None = None
    # pause_goal fields
    account_id: int | None = None
    recovered: int | None = None


class GoalAsk(BaseModel):
    """One active goal's evaluated monthly ask - what the month equation
    subtracts for it, and what pausing it would recover."""

    account_id: int
    label: str
    monthly_need: int


class BudgetTrimPlan(BaseModel):
    """The close-the-gap plan: pause/cut rows plus the residual neither
    tier covers (the part of the gap that belongs to bills or income)."""

    cuts: list[BudgetTrimResponse] = Field(default_factory=list)
    residual: int = 0


class BudgetSummaryResponse(BaseModel):
    period_month: int  # YYYYMM
    buckets: list[BudgetBucketResponse]
    stats: BudgetStatsResponse
    # Present (non-empty) exactly when the month lands negative and the
    # budgets have slack to give.
    trims: list[BudgetTrimResponse] = Field(default_factory=list)

    def bucket(
        self, name: Literal["fixed", "non_monthly", "one_time", "flexible"]
    ) -> BudgetBucketResponse:
        """One section by name. An absent one reads as empty."""
        return next(
            (b for b in self.buckets if b.name == name),
            BudgetBucketResponse(name=name, total_allocated=0, total_spent=0, lines=[]),
        )


class StatDetailRow(BaseModel):
    """One row of a header cell's click-through detail.

    Data only - the popup composes its own captions from these fields
    (a sub-monthly bill's cadence and face value; the row count behind
    an everything-else average).
    """

    label: str
    value: int  # cents
    frequency: str | None = None
    per_period_amount: int | None = None  # cents, sub-monthly bills only
    transaction_count: int | None = None


class BudgetStatDetailsResponse(BaseModel):
    """Per-row backup for the Budget header's fetched cells.

    ``window_start``/``window_end`` bound the everything-else average
    (``[start, end)``); the popup renders the human label.
    """

    income: list[StatDetailRow]
    bills: list[StatDetailRow]
    everything_else: list[StatDetailRow]
    # Spending seen in only one month of the window: face values, not
    # rates, listed apart so the two are never added together.
    one_offs: list[StatDetailRow] = []
    window_start: date
    window_end: date


class GoalParseRequest(BaseModel):
    text: str


class GoalParseResponse(BaseModel):
    """A deterministic, substring+regex reading of a natural-language goal
    ("I wanna cut back on Starbucks") - a preview, not a write. The caller
    decides whether to apply it (via POST /budget/lines)."""

    matched: bool
    target_type: Literal["category", "payee"] | None = None
    category_id: int | None = None
    payee_key: str | None = None
    payee_label: str | None = None
    baseline_monthly: int | None = None
    suggested_limit: int | None = None
    # Display name of whatever matched (payee label or category name) and
    # the cut fraction applied - the frontend writes the sentence.
    label: str | None = None
    fraction: float | None = None
    # The window the baseline averages, in days.
    baseline_days: int


class SuggestionDismissResult(BaseModel):
    dismissed: int


class SuggestionRestoreResult(BaseModel):
    restored: int
