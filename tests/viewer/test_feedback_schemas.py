from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackBundle,
    FeedbackCommand,
    FeedbackRecord,
    FeedbackStoreState,
    FeedbackWatermark,
    InterestFeedbackProjection,
    canonical_feedback_bundle_digest,
    normalize_feedback_paper_id,
)


NOW = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
MAX_SAFE_SEQUENCE = 9_007_199_254_740_991


def _command(**overrides: object) -> FeedbackCommand:
    payload: dict[str, object] = {
        "command_id": UUID("00000000-0000-4000-8000-000000000001"),
        "paper_id": "2401.01234",
        "action": "set_read",
        "value": True,
        "occurred_at": NOW,
        "device_id": "device-a",
        "sequence": 1,
    }
    payload.update(overrides)
    return FeedbackCommand(**payload)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2401.01234", "2401.01234"),
        ("2401.01234v9", "2401.01234"),
        ("hep-th/9901001", "hep-th/9901001"),
        ("hep-th/9901001v2", "hep-th/9901001"),
        ("ARXIV:2401.01234v1", "2401.01234"),
    ],
)
def test_normalize_feedback_paper_id_accepts_only_canonical_arxiv_forms(
    value: str, expected: str
) -> None:
    assert normalize_feedback_paper_id(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://arxiv.org/abs/2401.01234",
        "../2401.01234",
        "2401/01234",
        "2401.01234\n",
        "\\\\server\\share\\2401.01234",
        "C:\\papers\\2401.01234",
        "not an arxiv id",
    ],
)
def test_normalize_feedback_paper_id_rejects_paths_urls_and_control_text(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_feedback_paper_id(value)


def test_feedback_models_are_frozen_strict_and_normalize_utc_timestamps() -> None:
    command = _command(occurred_at=datetime(2026, 7, 22, 12, 0, tzinfo=timezone(timedelta(hours=8))))

    assert command.occurred_at == NOW
    with pytest.raises(ValidationError):
        FeedbackCommand.model_validate({**command.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        _command(occurred_at=datetime(2026, 7, 22, 4, 0))
    with pytest.raises(ValidationError):
        command.sequence = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_id", "not-a-uuid"),
        ("command_id", UUID("00000000-0000-0000-0000-000000000000")),
        ("device_id", "device id"),
        ("device_id", "\\\\server"),
        ("sequence", 0),
        ("sequence", -1),
        ("sequence", True),
        ("value", "true"),
    ],
)
def test_feedback_command_rejects_invalid_identity_and_scalar_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _command(**{field: value})


def test_feedback_command_sequence_is_limited_to_the_cross_runtime_safe_integer_range() -> None:
    assert _command(sequence=MAX_SAFE_SEQUENCE).sequence == MAX_SAFE_SEQUENCE
    with pytest.raises(ValidationError):
        _command(sequence=0)
    with pytest.raises(ValidationError):
        _command(sequence=MAX_SAFE_SEQUENCE + 1)
    with pytest.raises(ValidationError):
        FeedbackWatermark(
            occurred_at=NOW,
            device_id="device-a",
            sequence=0,
            command_id=UUID("00000000-0000-4000-8000-000000000001"),
        )


def test_bundle_requires_safe_digest_and_canonical_command_order() -> None:
    first = _command(sequence=1)
    second = _command(
        command_id=UUID("00000000-0000-4000-8000-000000000002"), sequence=2
    )
    bundle_id = UUID("00000000-0000-4000-8000-000000000003")
    digest = canonical_feedback_bundle_digest(
        (first, second), bundle_id=bundle_id, generated_at=NOW
    )

    bundle = FeedbackBundle(
        bundle_id=bundle_id, generated_at=NOW, commands=(first, second), digest=digest
    )

    assert bundle.commands == (first, second)
    with pytest.raises(ValidationError, match="digest"):
        FeedbackBundle(
            bundle_id=uuid4(), generated_at=NOW, commands=(first,), digest="A" * 64
        )
    with pytest.raises(ValidationError, match="canonical"):
        FeedbackBundle(
            bundle_id=uuid4(), generated_at=NOW, commands=(second, first), digest=digest
        )


def test_bundle_digest_binds_bundle_identity_and_generation_time() -> None:
    command = _command()
    bundle_id = UUID("00000000-0000-4000-8000-000000000010")
    digest = canonical_feedback_bundle_digest(
        (command,), bundle_id=bundle_id, generated_at=NOW
    )

    FeedbackBundle(bundle_id=bundle_id, generated_at=NOW, commands=(command,), digest=digest)
    with pytest.raises(ValidationError, match="digest"):
        FeedbackBundle(
            bundle_id=UUID("00000000-0000-4000-8000-000000000011"),
            generated_at=NOW,
            commands=(command,),
            digest=digest,
        )


def test_collections_are_bounded_and_canonically_ordered() -> None:
    record = FeedbackRecord(paper_id="2401.01234")
    state = FeedbackStoreState(
        records=(record,),
        applied_command_ids=(UUID("00000000-0000-4000-8000-000000000001"),),
    )
    projection = InterestFeedbackProjection(
        read_ids=("hep-th/9901001v3", "2401.01234v2"),
        favorite_ids=("2401.01234v1",),
    )

    assert state.records == (record,)
    assert projection.read_ids == ("2401.01234", "hep-th/9901001")
    with pytest.raises(ValidationError, match="canonical"):
        FeedbackStoreState(records=(FeedbackRecord(paper_id="hep-th/9901001"), record))
    with pytest.raises(ValidationError, match="at most"):
        InterestFeedbackProjection(read_ids=tuple(f"2401.{index:05d}" for index in range(501)))
