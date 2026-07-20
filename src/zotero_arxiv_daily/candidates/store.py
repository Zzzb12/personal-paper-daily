from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from zotero_arxiv_daily.analysis.schemas import CandidateBatch


@dataclass(frozen=True)
class StoredCandidateBatch:
    path: Path
    sha256: str


class CandidateStore:
    """Persist schema-validated candidate batches as atomic UTF-8 JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, run_id: str) -> Path:
        normalized = run_id.strip()
        if not normalized or normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
            raise ValueError("run_id must be a safe file name")
        return self.root / f"{normalized}.json"

    def write(self, batch: CandidateBatch) -> StoredCandidateBatch:
        target = self.path_for(batch.run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        data = batch.to_deterministic_json().encode("utf-8")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            CandidateBatch.model_validate_json(temporary.read_text(encoding="utf-8"))
            os.replace(temporary, target)
            return StoredCandidateBatch(path=target, sha256=hashlib.sha256(data).hexdigest())
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, run_id: str) -> CandidateBatch:
        return CandidateBatch.model_validate_json(self.path_for(run_id).read_text(encoding="utf-8"))
