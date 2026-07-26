from pathlib import Path

import pytest

from zotero_arxiv_daily.candidates.feedback import (
    FeedbackProjectionError,
    FeedbackProjectionLoader,
)
from zotero_arxiv_daily.viewer.feedback import (
    FeedbackRecord,
    FeedbackStore,
    FeedbackStoreSafetyError,
    FeedbackStoreState,
)


class _Store:
    def __init__(self, state: FeedbackStoreState) -> None:
        self.state = state

    def read(self) -> FeedbackStoreState:
        return self.state


def test_projection_loader_projects_only_states_that_affect_ranking() -> None:
    projection = FeedbackProjectionLoader(
        _Store(
            FeedbackStoreState(
                records=(
                    FeedbackRecord(paper_id="2401.00001v2", read=True),
                    FeedbackRecord(paper_id="2401.00002", favorite=True),
                    FeedbackRecord(paper_id="2401.00003", irrelevant=True),
                )
            )
        ),
        favorite_delta=0.05,
    ).load()

    assert projection.read_ids == ("2401.00001",)
    assert projection.favorite_ids == ("2401.00002",)
    assert projection.irrelevant_ids == ("2401.00003",)
    assert projection.favorite_delta == 0.05


def test_missing_optional_store_is_an_empty_neutral_projection(tmp_path: Path) -> None:
    projection = FeedbackProjectionLoader.from_optional_path(
        None, root=tmp_path, favorite_delta=0.10
    ).load()

    assert projection.read_ids == ()
    assert projection.favorite_ids == ()
    assert projection.irrelevant_ids == ()
    assert projection.favorite_delta == 0.10


def test_configured_corrupt_store_fails_with_fixed_safe_error(tmp_path: Path) -> None:
    store_path = tmp_path / "private" / "feedback-store.json"
    store_path.parent.mkdir()
    store_path.write_text("not-json private-content", encoding="utf-8")
    loader = FeedbackProjectionLoader(
        FeedbackStore(store_path, root=tmp_path), favorite_delta=0.05
    )

    with pytest.raises(FeedbackProjectionError) as error:
        loader.load()

    assert str(error.value) == "feedback projection rejected"
    assert "private-content" not in str(error.value)


def test_loader_rejects_invalid_store_results_without_leaking_details() -> None:
    class _UnsafeStore:
        def read(self):
            raise FeedbackStoreSafetyError()

    with pytest.raises(FeedbackProjectionError, match="^feedback projection rejected$"):
        FeedbackProjectionLoader(_UnsafeStore()).load()


def test_loader_replaces_unexpected_store_failures_with_the_fixed_safe_error() -> None:
    class _ExplosiveStore:
        def read(self):
            raise RuntimeError("private path C:/users/alice/2401.00001")

    with pytest.raises(FeedbackProjectionError) as error:
        FeedbackProjectionLoader(_ExplosiveStore()).load()

    assert str(error.value) == "feedback projection rejected"
