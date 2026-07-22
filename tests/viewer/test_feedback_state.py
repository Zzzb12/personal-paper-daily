from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackCommand,
    FeedbackStoreState,
    apply_feedback_commands,
)


NOW = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)


def _command(
    *,
    index: int,
    action: str,
    value: bool,
    paper_id: str = "2401.01234",
    seconds: int = 0,
    device_id: str = "device-a",
    sequence: int | None = None,
) -> FeedbackCommand:
    return FeedbackCommand(
        command_id=UUID(f"00000000-0000-4000-8000-{index:012d}"),
        paper_id=paper_id,
        action=action,  # type: ignore[arg-type]
        value=value,
        occurred_at=NOW + timedelta(seconds=seconds),
        device_id=device_id,
        sequence=index if sequence is None else sequence,
    )


def test_read_coexists_with_favorite_and_irrelevant_states() -> None:
    result = apply_feedback_commands(
        FeedbackStoreState(),
        (
            _command(index=1, action="set_read", value=True),
            _command(index=2, action="set_favorite", value=True, seconds=1),
            _command(index=3, action="set_irrelevant", value=True, seconds=2),
        ),
    )

    record = result.state.records[0]
    assert (record.read, record.favorite, record.irrelevant) == (True, False, True)
    assert result.conflict_count == 1


def test_clear_only_changes_its_requested_preference_state() -> None:
    initial = apply_feedback_commands(
        FeedbackStoreState(),
        (
            _command(index=1, action="set_read", value=True),
            _command(index=2, action="set_favorite", value=True, seconds=1),
        ),
    ).state

    result = apply_feedback_commands(
        initial, (_command(index=3, action="set_favorite", value=False, seconds=2),)
    )

    record = result.state.records[0]
    assert (record.read, record.favorite, record.irrelevant) == (True, False, False)


def test_duplicate_command_is_a_no_op_and_does_not_mutate_inputs() -> None:
    command = _command(index=1, action="set_read", value=True)
    original = FeedbackStoreState()
    first = apply_feedback_commands(original, (command,))
    second = apply_feedback_commands(first.state, (command,))

    assert original.records == ()
    assert first.state.records[0].read is True
    assert second.state == first.state
    assert second.duplicate_count == 1
    assert second.applied_count == 0


def test_stale_command_cannot_override_a_domain_watermark() -> None:
    current = apply_feedback_commands(
        FeedbackStoreState(),
        (_command(index=2, action="set_favorite", value=True, seconds=2),),
    ).state

    result = apply_feedback_commands(
        current, (_command(index=1, action="set_irrelevant", value=True, seconds=1),)
    )

    assert result.state.records[0].favorite is True
    assert result.state.records[0].irrelevant is False
    assert result.stale_count == 1


def test_command_order_and_bundle_import_order_produce_the_same_state() -> None:
    commands = (
        _command(index=1, action="set_favorite", value=True, seconds=1, device_id="device-b"),
        _command(index=2, action="set_read", value=True, seconds=2),
        _command(index=3, action="set_irrelevant", value=True, seconds=3),
    )

    first = apply_feedback_commands(FeedbackStoreState(), commands)
    second = apply_feedback_commands(FeedbackStoreState(), tuple(reversed(commands)))

    assert first.state == second.state
    assert first.applied_count == second.applied_count == 3
    assert first.conflict_count == second.conflict_count == 1


def test_sequential_bundle_imports_converge_stale_command_dedupe_ledger() -> None:
    earlier = _command(index=1, action="set_irrelevant", value=True, seconds=1)
    later = _command(index=2, action="set_favorite", value=True, seconds=2)

    earlier_then_later = apply_feedback_commands(
        apply_feedback_commands(FeedbackStoreState(), (earlier,)).state, (later,)
    ).state
    later_then_earlier = apply_feedback_commands(
        apply_feedback_commands(FeedbackStoreState(), (later,)).state, (earlier,)
    ).state

    assert earlier_then_later == later_then_earlier
    replay = apply_feedback_commands(later_then_earlier, (earlier, later))
    assert replay.duplicate_count == 2
    assert replay.stale_count == 0


def test_same_value_advances_watermark_without_changing_boolean_state() -> None:
    initial = apply_feedback_commands(
        FeedbackStoreState(), (_command(index=1, action="set_read", value=True, seconds=1),)
    ).state

    result = apply_feedback_commands(
        initial, (_command(index=2, action="set_read", value=True, seconds=2),)
    )

    assert result.state.records[0].read is True
    assert result.state.records[0].read_watermark.sequence == 2
    assert result.applied_count == 1
    assert result.changed_count == 0
