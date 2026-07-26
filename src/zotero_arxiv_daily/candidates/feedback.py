"""Private-feedback projection boundary for candidate ranking."""

from pathlib import Path

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackStore,
    FeedbackStoreState,
    InterestFeedbackProjection,
)


FEEDBACK_PROJECTION_IMPLEMENTATION_VERSION = "stage8-feedback-projection-v2"


class FeedbackProjectionError(ValueError):
    def __init__(self) -> None:
        super().__init__("feedback projection rejected")


class FeedbackProjectionLoader:
    def __init__(self, store: object | None, *, favorite_delta: float = 0.05) -> None:
        self.store = store
        self.favorite_delta = favorite_delta

    @classmethod
    def from_optional_path(
        cls, path: Path | None, *, root: Path, favorite_delta: float = 0.05
    ) -> "FeedbackProjectionLoader":
        store = None if path is None else FeedbackStore(path, root=root)
        return cls(store, favorite_delta=favorite_delta)

    def load(self) -> InterestFeedbackProjection:
        if self.store is None:
            return InterestFeedbackProjection(favorite_delta=self.favorite_delta)
        try:
            state = self.store.read()
            state = FeedbackStoreState.model_validate(state)
        except Exception as error:
            raise FeedbackProjectionError() from error
        return InterestFeedbackProjection(
            read_ids=tuple(record.paper_id for record in state.records if record.read),
            favorite_ids=tuple(record.paper_id for record in state.records if record.favorite),
            irrelevant_ids=tuple(record.paper_id for record in state.records if record.irrelevant),
            favorite_delta=self.favorite_delta,
        )
