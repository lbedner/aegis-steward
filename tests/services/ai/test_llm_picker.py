"""The chat composer's model selector: pure display/grouping helpers."""

from app.services.ai.domains.llm.picker import (
    family_display_name,
    format_price,
    group_models,
    is_local_model,
    model_label,
    newest_first,
)


class TestModelLabel:
    def test_shows_the_resolved_model_clipped(self) -> None:
        assert model_label({"model": "qwen3.8:27b-mlx"}) == "qwen3.8:27b-mlx"
        long = {"model": "anthropic/claude-sonnet-4-5-20250929-extended"}
        assert len(model_label(long)) == 24
        assert model_label(long).endswith("...")

    def test_placeholder_when_nothing_resolved(self) -> None:
        assert model_label(None) == "model"
        assert model_label({}) == "model"


class TestNewestFirst:
    def test_sorts_by_release_date_descending_undated_last(self) -> None:
        models = [
            {"model_id": "old", "released_on": "2024-01-01"},
            {"model_id": "undated", "released_on": None},
            {"model_id": "new", "released_on": "2026-06-01"},
        ]

        ordered = newest_first(models)

        assert [m["model_id"] for m in ordered] == ["new", "old", "undated"]


class TestGroupModels:
    _models = [
        {
            "model_id": "gpt-4o",
            "vendor": "OpenAI",
            "family": "gpt-4o",
            "released_on": "2024-05-13",
        },
        {
            "model_id": "claude-s4",
            "vendor": "Anthropic",
            "family": "claude-4",
            "released_on": "2025-05-22",
        },
        {
            "model_id": "gpt-4o-mini",
            "vendor": "OpenAI",
            "family": "gpt-4o",
            "released_on": "2024-07-18",
        },
        {"model_id": "mystery", "vendor": None, "family": None, "released_on": None},
    ]

    def test_groups_by_vendor_newest_first_within(self) -> None:
        grouped = group_models(self._models, by="vendor")

        assert [name for name, _rows in grouped] == [
            "Anthropic",
            "OpenAI",
            "Other",
        ]
        openai = dict(grouped)["OpenAI"]
        assert [m["model_id"] for m in openai] == ["gpt-4o-mini", "gpt-4o"]

    def test_groups_by_family_with_display_names(self) -> None:
        grouped = group_models(self._models, by="family")

        names = [name for name, _rows in grouped]
        assert "Claude 4" in names
        assert "Gpt 4o" in names
        assert names[-1] == "Other"


class TestFamilyDisplayName:
    def test_prettifies_slugs(self) -> None:
        assert family_display_name("claude-3.5") == "Claude 3.5"
        assert family_display_name("llama-3.2") == "Llama 3.2"
        assert family_display_name(None) == "Other"


class TestFormatContextWindow:
    def test_compact_units(self) -> None:
        from app.services.ai.domains.llm.picker import format_context_window

        assert format_context_window(8_192) == "8k"
        assert format_context_window(128_000) == "128k"
        assert format_context_window(200_000) == "200k"
        assert format_context_window(1_048_576) == "1M"
        assert format_context_window(2_000_000) == "2M"

    def test_unknown_is_blank(self) -> None:
        from app.services.ai.domains.llm.picker import format_context_window

        assert format_context_window(0) == ""
        assert format_context_window(None) == ""


class TestFormatPrice:
    def test_in_and_out_per_million(self) -> None:
        from app.services.ai.domains.llm.picker import format_price

        assert format_price(1.25, 10.0) == "$1.25 / $10"
        assert format_price(3.0, 15.0) == "$3 / $15"

    def test_partial_or_missing_is_blank_or_single(self) -> None:
        from app.services.ai.domains.llm.picker import format_price

        assert format_price(None, None) == ""
        assert format_price(0.5, None) == "$0.50"


class TestFilterModels:
    def test_matches_id_title_and_vendor_case_insensitive(self) -> None:
        from app.services.ai.domains.llm.picker import filter_models

        models = [
            {"model_id": "gpt-5.6-terra", "title": "GPT-5.6 Terra", "vendor": "openai"},
            {"model_id": "claude-opus-5", "title": "Opus 5", "vendor": "anthropic"},
        ]
        assert [m["model_id"] for m in filter_models(models, "opus")] == [
            "claude-opus-5"
        ]
        assert [m["model_id"] for m in filter_models(models, "OPENAI")] == [
            "gpt-5.6-terra"
        ]
        assert filter_models(models, "") == models


class TestDisplayTitle:
    def test_strips_the_vendors_own_prefix_under_its_section(self) -> None:
        from app.services.ai.domains.llm.picker import display_title

        model = {"title": "OpenAI: GPT-5.6 Luna", "vendor": "openai"}
        assert display_title(model, under_vendor="openai") == "GPT-5.6 Luna"

    def test_keeps_the_prefix_in_flat_views(self) -> None:
        from app.services.ai.domains.llm.picker import display_title

        model = {"title": "OpenAI: GPT-5.6 Luna", "vendor": "openai"}
        assert display_title(model, under_vendor=None) == "OpenAI: GPT-5.6 Luna"

    def test_leaves_unprefixed_titles_alone(self) -> None:
        from app.services.ai.domains.llm.picker import display_title

        model = {"title": "Gpt Oss:20B", "vendor": "ollama", "model_id": "gpt-oss:20b"}
        assert display_title(model, under_vendor="ollama") == "Gpt Oss:20B"


class TestLabForModel:
    """The lab is DATA now - resolved from the model registry at sync
    time and carried on the row - not a prefix table that goes stale the
    week a lab ships under a new product name."""

    def test_it_reads_the_resolved_lab(self) -> None:
        from app.services.ai.domains.llm.picker import lab_for_model

        model = {"model_id": "muse-glimmer:30b-mlx", "lab": "Meta Inc."}
        assert lab_for_model(model) == "Meta Inc."

    def test_an_unresolved_model_has_no_lab(self) -> None:
        from app.services.ai.domains.llm.picker import lab_for_model

        assert lab_for_model({"model_id": "my-private-merge"}) is None
        assert lab_for_model({"model_id": "x", "lab": None}) is None


class TestModelPickerDialog:
    """Dialog behavior: picking keeps it open (compare, then leave), and
    the barrier is dismissable - clicking outside closes it."""

    @staticmethod
    def _dialog():
        from app.components.frontend.controls.chat.model_picker import (
            ModelPickerDialog,
        )

        async def _noop(*_args: object) -> None:
            return None

        return ModelPickerDialog(
            models=[
                {"model_id": "a-1", "title": "A One", "vendor": "acme"},
                {"model_id": "b-2", "title": "B Two", "vendor": "acme"},
            ],
            active_id="a-1",
            on_pick=_noop,
            on_close=_noop,
        )

    def test_outside_click_can_dismiss(self) -> None:
        assert self._dialog().modal is False

    def test_set_active_moves_the_marker_without_closing(self) -> None:
        dialog = self._dialog()
        before = dialog._list_host.content

        dialog.set_active("b-2")

        assert dialog._active_id == "b-2"
        assert dialog._list_host.content is not before  # rows re-rendered


class TestALocalModelIsFreeNotUnpriced:
    """A missing price means two different things. For a cloud model it
    is a gap in the catalog - we do not know what it costs, and a blank
    admits that. For a local model the blank WAS the answer: it runs on
    this machine and costs nothing, and leaving the only free option
    looking like the one we failed to price gets it read as the risky
    choice rather than the free one."""

    def test_a_local_model_costs_nothing_and_says_so(self) -> None:
        model = {"vendor": "ollama", "input_price": None, "output_price": None}

        assert is_local_model(model)
        assert format_price(None, None, local=True) == "$0.00"

    def test_the_zero_keeps_its_cents(self) -> None:
        """``_usd(0)`` renders "$0", and trailing zeros are what make a
        figure read as a price - "$0" beside "$3 / $15" reads like a
        value that failed to load."""
        assert format_price(None, None, local=True) == "$0.00"

    def test_a_cloud_model_with_no_pricing_stays_blank(self) -> None:
        """Inventing a figure is worse than admitting we lack one."""
        model = {"vendor": "openai", "input_price": None, "output_price": None}

        assert not is_local_model(model)
        assert format_price(None, None, local=is_local_model(model)) == ""

    def test_a_priced_model_is_untouched_whatever_it_runs_on(self) -> None:
        assert format_price(3.0, 15.0, local=True) == "$3 / $15"

    def test_the_vendor_check_ignores_case(self) -> None:
        assert is_local_model({"vendor": "Ollama"})
        assert not is_local_model({})


class TestTheFooterOnAFinishedTurn:
    """The attribution line under a settled message. Same rule as the
    picker row, one line further on: a local turn is free and says so, a
    cloud turn with no cost recorded says nothing."""

    def test_a_local_turn_is_free(self) -> None:
        from app.core.chat_transcript import footer_line

        meta = {"model": "muse-glimmer:30b-mlx-128k", "provider": "ollama"}

        assert footer_line(meta, local=is_local_model(meta)).endswith("$0.00")

    def test_a_cloud_turn_with_no_cost_recorded_says_nothing(self) -> None:
        """A blank admits we do not know what the turn cost; a figure
        would be invented."""
        from app.core.chat_transcript import footer_line

        meta = {"model": "gpt-4o", "provider": "openai"}

        assert "$" not in footer_line(meta, local=is_local_model(meta))

    def test_a_recorded_cost_still_wins(self) -> None:
        from app.core.chat_transcript import footer_line

        assert "$0.0123" in footer_line({"model": "gpt-4o", "cost": 0.0123})

    def test_provider_is_read_as_well_as_vendor(self) -> None:
        """A catalog row spells it ``vendor``; a finished message's
        metadata spells it ``provider``. Same question."""
        assert is_local_model({"provider": "ollama"})
        assert is_local_model({"vendor": "ollama"})
