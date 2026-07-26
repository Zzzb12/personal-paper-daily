"""Strict, private reader-feedback contracts and deterministic merge rules."""

from __future__ import annotations

import hashlib
import json
import errno
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel


FEEDBACK_SCHEMA_VERSION = "1.0"
MAX_FEEDBACK_COMMANDS = 500
MAX_FEEDBACK_RECORDS = 500
MAX_APPLIED_COMMAND_IDS = 1_000
MAX_APPLIED_BUNDLE_IDS = 100
MAX_FEEDBACK_STORE_BYTES = 1_000_000
MAX_FEEDBACK_BUNDLE_BYTES = 1_000_000
MAX_FEEDBACK_SEQUENCE = 9_007_199_254_740_991

_ARXIV_ID_RE = re.compile(
    r"^(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v[1-9]\d*)?$", re.IGNORECASE
)
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _make_feedback_temp(directory: str, prefix: str, suffix: str) -> tuple[int, str]:
    return tempfile.mkstemp(dir=directory, prefix=prefix, suffix=suffix)


def _ensure_feedback_parent(directory: str) -> None:
    Path(directory).mkdir(parents=True, exist_ok=True)


def _directory_sync_is_unsupported(error: OSError) -> bool:
    unsupported = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
    if error.errno in unsupported:
        return True
    return os.name == "nt" and error.errno in {errno.EACCES, errno.EPERM}


def _sync_feedback_directory(directory: str) -> None:
    """Synchronize metadata, swallowing only errno values that prove it unsupported."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError as error:
        if _directory_sync_is_unsupported(error):
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as error:
        if _directory_sync_is_unsupported(error):
            return
        raise
    finally:
        os.close(descriptor)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _validate_v4_uuid(value: UUID) -> UUID:
    if value.version != 4:
        raise ValueError("identifier must be a UUID4")
    return value


def _validate_device_id(value: str) -> str:
    if not _DEVICE_ID_RE.fullmatch(value):
        raise ValueError("device_id must be a safe identifier")
    return value


def normalize_feedback_paper_id(value: str) -> str:
    """Return a stable arXiv identifier without its optional version suffix."""
    if not isinstance(value, str) or any(ord(character) < 32 for character in value):
        raise ValueError("paper_id must be a canonical arXiv identifier")
    normalized = value.strip()
    if normalized.lower().startswith("arxiv:"):
        normalized = normalized[6:]
    if not _ARXIV_ID_RE.fullmatch(normalized):
        raise ValueError("paper_id must be a canonical arXiv identifier")
    return re.sub(r"v[1-9]\d*$", "", normalized, flags=re.IGNORECASE).lower()


class FeedbackWatermark(StrictModel):
    occurred_at: datetime
    device_id: str
    sequence: StrictInt = Field(ge=1, le=MAX_FEEDBACK_SEQUENCE)
    command_id: UUID

    @field_validator("occurred_at")
    @classmethod
    def require_aware_occurred_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @field_validator("device_id")
    @classmethod
    def require_safe_device_id(cls, value: str) -> str:
        return _validate_device_id(value)

    @field_validator("sequence")
    @classmethod
    def reject_boolean_sequence(cls, value: int) -> int:
        if isinstance(value, bool):
            raise ValueError("sequence must be an integer")
        return value

    @field_validator("command_id")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        return _validate_v4_uuid(value)

    @property
    def sort_key(self) -> tuple[datetime, str, int, str]:
        return (self.occurred_at, self.device_id, self.sequence, str(self.command_id))


class FeedbackCommand(StrictModel):
    schema_version: Literal["1.0"] = FEEDBACK_SCHEMA_VERSION
    command_id: UUID
    paper_id: str
    action: Literal["set_read", "set_favorite", "set_irrelevant"]
    value: StrictBool
    occurred_at: datetime
    device_id: str
    sequence: StrictInt = Field(ge=1, le=MAX_FEEDBACK_SEQUENCE)

    @field_validator("command_id")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        return _validate_v4_uuid(value)

    @field_validator("paper_id")
    @classmethod
    def normalize_paper_id(cls, value: str) -> str:
        return normalize_feedback_paper_id(value)

    @field_validator("occurred_at")
    @classmethod
    def require_aware_occurred_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @field_validator("device_id")
    @classmethod
    def require_safe_device_id(cls, value: str) -> str:
        return _validate_device_id(value)

    @field_validator("sequence")
    @classmethod
    def reject_boolean_sequence(cls, value: int) -> int:
        if isinstance(value, bool):
            raise ValueError("sequence must be an integer")
        return value

    @property
    def watermark(self) -> FeedbackWatermark:
        return FeedbackWatermark(
            occurred_at=self.occurred_at,
            device_id=self.device_id,
            sequence=self.sequence,
            command_id=self.command_id,
        )

    @property
    def sort_key(self) -> tuple[datetime, str, int, str]:
        return self.watermark.sort_key


class FeedbackRecord(StrictModel):
    schema_version: Literal["1.0"] = FEEDBACK_SCHEMA_VERSION
    paper_id: str
    read: StrictBool = False
    favorite: StrictBool = False
    irrelevant: StrictBool = False
    read_watermark: FeedbackWatermark | None = None
    preference_watermark: FeedbackWatermark | None = None

    @field_validator("paper_id")
    @classmethod
    def normalize_paper_id(cls, value: str) -> str:
        return normalize_feedback_paper_id(value)

    @model_validator(mode="after")
    def prevent_conflicting_preferences(self) -> Self:
        if self.favorite and self.irrelevant:
            raise ValueError("favorite and irrelevant are mutually exclusive")
        return self


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def canonical_feedback_bundle_digest(
    commands: Iterable[FeedbackCommand], *, bundle_id: UUID, generated_at: datetime
) -> str:
    """Hash the complete canonical bundle payload except its digest itself."""
    payload = {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "bundle_id": str(bundle_id),
        "generated_at": _utc_datetime(generated_at).isoformat().replace("+00:00", "Z"),
        "commands": [command.model_dump(mode="json") for command in commands],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class FeedbackBundle(StrictModel):
    schema_version: Literal["1.0"] = FEEDBACK_SCHEMA_VERSION
    bundle_id: UUID
    generated_at: datetime
    commands: tuple[FeedbackCommand, ...]
    digest: str

    @field_validator("bundle_id")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        return _validate_v4_uuid(value)

    @field_validator("generated_at")
    @classmethod
    def require_aware_generated_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @field_validator("commands")
    @classmethod
    def require_canonical_commands(
        cls, value: tuple[FeedbackCommand, ...]
    ) -> tuple[FeedbackCommand, ...]:
        if len(value) > MAX_FEEDBACK_COMMANDS:
            raise ValueError(f"commands may contain at most {MAX_FEEDBACK_COMMANDS} entries")
        if tuple(sorted(value, key=lambda command: command.sort_key)) != value:
            raise ValueError("commands must use canonical ordering")
        if len({command.command_id for command in value}) != len(value):
            raise ValueError("commands must not repeat command IDs")
        return value

    @field_validator("digest")
    @classmethod
    def require_safe_digest(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("digest must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def verify_digest(self) -> Self:
        if self.digest != canonical_feedback_bundle_digest(
            self.commands, bundle_id=self.bundle_id, generated_at=self.generated_at
        ):
            raise ValueError("digest must match canonical commands")
        return self


def _require_sorted_unique_uuids(value: tuple[UUID, ...], field_name: str, limit: int) -> tuple[UUID, ...]:
    if len(value) > limit:
        raise ValueError(f"{field_name} may contain at most {limit} entries")
    if any(identifier.version != 4 for identifier in value):
        raise ValueError(f"{field_name} must contain UUID4 values")
    if tuple(sorted(value, key=str)) != value or len(set(value)) != len(value):
        raise ValueError(f"{field_name} must use canonical ordering without duplicates")
    return value


class FeedbackStoreState(StrictModel):
    schema_version: Literal["1.0"] = FEEDBACK_SCHEMA_VERSION
    records: tuple[FeedbackRecord, ...] = ()
    applied_command_ids: tuple[UUID, ...] = ()
    applied_bundle_ids: tuple[UUID, ...] = ()

    @field_validator("records")
    @classmethod
    def require_canonical_records(cls, value: tuple[FeedbackRecord, ...]) -> tuple[FeedbackRecord, ...]:
        if len(value) > MAX_FEEDBACK_RECORDS:
            raise ValueError(f"records may contain at most {MAX_FEEDBACK_RECORDS} entries")
        if tuple(sorted(value, key=lambda record: record.paper_id)) != value:
            raise ValueError("records must use canonical ordering")
        if len({record.paper_id for record in value}) != len(value):
            raise ValueError("records must not repeat paper IDs")
        return value

    @field_validator("applied_command_ids")
    @classmethod
    def require_canonical_command_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return _require_sorted_unique_uuids(value, "applied_command_ids", MAX_APPLIED_COMMAND_IDS)

    @field_validator("applied_bundle_ids")
    @classmethod
    def require_canonical_bundle_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return _require_sorted_unique_uuids(value, "applied_bundle_ids", MAX_APPLIED_BUNDLE_IDS)


class InterestFeedbackProjection(StrictModel):
    schema_version: Literal["1.0"] = FEEDBACK_SCHEMA_VERSION
    read_ids: tuple[str, ...] = ()
    favorite_ids: tuple[str, ...] = ()
    irrelevant_ids: tuple[str, ...] = ()
    favorite_delta: float = Field(default=0.05, ge=0.0, le=0.10)

    @field_validator("read_ids", "favorite_ids", "irrelevant_ids")
    @classmethod
    def require_canonical_paper_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(normalize_feedback_paper_id(paper_id) for paper_id in value)
        if len(normalized) > MAX_FEEDBACK_RECORDS:
            raise ValueError(f"paper ID collections may contain at most {MAX_FEEDBACK_RECORDS} entries")
        if len(set(normalized)) != len(normalized):
            raise ValueError("paper ID collections must not contain duplicates")
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def prevent_conflicting_projection_preferences(self) -> Self:
        if set(self.favorite_ids) & set(self.irrelevant_ids):
            raise ValueError("favorite_ids and irrelevant_ids must not overlap")
        return self


class FeedbackMergeResult(StrictModel):
    state: FeedbackStoreState
    applied_count: int = Field(ge=0)
    changed_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)


class FeedbackStoreSafetyError(ValueError):
    """Fixed, non-sensitive failure returned by private feedback boundaries."""

    def __init__(self) -> None:
        super().__init__("feedback store rejected")


@dataclass(frozen=True)
class FeedbackStoreFileOps:
    """Replaceable filesystem seams for offline atomic-write tests."""

    open_file: Callable[..., BinaryIO]
    open_fd: Callable[..., BinaryIO]
    make_temp: Callable[[str, str, str], tuple[int, str]]
    fsync: Callable[[int], None]
    replace: Callable[[str, str], None]
    unlink: Callable[[str], None]
    lstat: Callable[[str], os.stat_result]
    sync_directory: Callable[[str], None]
    ensure_parent: Callable[[str], None]

    @classmethod
    def default(cls) -> Self:
        return cls(
            open_file=open,
            open_fd=os.fdopen,
            make_temp=_make_feedback_temp,
            fsync=os.fsync,
            replace=os.replace,
            unlink=os.unlink,
            lstat=os.lstat,
            sync_directory=_sync_feedback_directory,
            ensure_parent=_ensure_feedback_parent,
        )


class FeedbackImportResult(StrictModel):
    state: FeedbackStoreState
    applied_count: int = Field(ge=0)
    changed_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    bundle_duplicate_count: int = Field(ge=0)


def canonical_feedback_store_digest(state: FeedbackStoreState) -> str:
    """Hash the complete canonical state payload stored in the private envelope."""
    return hashlib.sha256(
        _canonical_json(state.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


def _has_windows_unsafe_prefix(path: Path, root: Path) -> bool:
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return True
    path_drive = path.drive.lower()
    root_drive = root.drive.lower()
    return bool(path_drive and path_drive != root_drive)


class FeedbackStore:
    """Private, bounded feedback state with fail-closed local file handling."""

    def __init__(
        self,
        path: Path | str,
        *,
        root: Path | str,
        max_bytes: int = MAX_FEEDBACK_STORE_BYTES,
        file_ops: FeedbackStoreFileOps | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.file_ops = file_ops or FeedbackStoreFileOps.default()
        self.clock = clock or (lambda: datetime.now(UTC))

    def _checked_path(self, path: Path) -> Path:
        if self.max_bytes < 1 or _has_windows_unsafe_prefix(path, self.root):
            raise FeedbackStoreSafetyError()
        if any(part == ".." for part in path.parts):
            raise FeedbackStoreSafetyError()
        root = self.root.absolute()
        self._check_root_ancestors(root)
        candidate = path if path.is_absolute() else root / path
        candidate = candidate.absolute()
        if _has_windows_unsafe_prefix(candidate, root):
            raise FeedbackStoreSafetyError()
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise FeedbackStoreSafetyError() from error
        if any(part == ".." for part in relative.parts):
            raise FeedbackStoreSafetyError()
        self._reject_symlinks_and_non_directories(root, relative, candidate)
        return candidate

    def checked_path(self) -> Path:
        """Return the validated local store path without reading or writing its contents."""
        return self._checked_path(self.path)

    def _reject_symlinks_and_non_directories(
        self, root: Path, relative: Path, candidate: Path
    ) -> None:
        current = root
        for part in relative.parts[:-1]:
            self._check_existing_path(current, directory=True)
            current = current / part
        self._check_existing_path(current, directory=True)
        self._check_existing_path(candidate, directory=False)

    def _check_root_ancestors(self, root: Path) -> None:
        for ancestor in reversed(root.parents):
            self._check_existing_path(ancestor, directory=True, required=True)

    def _check_existing_path(self, path: Path, *, directory: bool, required: bool = False) -> None:
        try:
            information = self.file_ops.lstat(str(path))
        except FileNotFoundError:
            if required:
                raise FeedbackStoreSafetyError()
            return
        except (OSError, ValueError) as error:
            raise FeedbackStoreSafetyError() from error
        attributes = getattr(information, "st_file_attributes", 0) or 0
        if stat.S_ISLNK(information.st_mode) or (attributes & 0x0400):
            raise FeedbackStoreSafetyError()
        if directory and not stat.S_ISDIR(information.st_mode):
            raise FeedbackStoreSafetyError()
        if not directory and not stat.S_ISREG(information.st_mode):
            raise FeedbackStoreSafetyError()

    def _read_bytes(self, path: Path, *, max_bytes: int) -> bytes | None:
        checked = self._checked_path(path)
        try:
            with self.file_ops.open_file(checked, "rb") as handle:
                contents = handle.read(max_bytes + 1)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise FeedbackStoreSafetyError() from error
        if len(contents) > max_bytes:
            raise FeedbackStoreSafetyError()
        self._checked_path(path)
        return contents

    def _state_from_payload(self, payload: object) -> tuple[FeedbackStoreState, bool]:
        if not isinstance(payload, dict):
            raise FeedbackStoreSafetyError()
        version = payload.get("schema_version")
        if version == "0.0":
            try:
                state = FeedbackStoreState(
                    records=tuple(payload["records"]),
                    applied_command_ids=tuple(payload.get("applied_command_ids", ())),
                    applied_bundle_ids=tuple(payload.get("applied_bundle_ids", ())),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise FeedbackStoreSafetyError() from error
            return state, True
        if version != FEEDBACK_SCHEMA_VERSION:
            raise FeedbackStoreSafetyError()
        try:
            state_payload = payload["state"]
            digest = payload["digest"]
            state = FeedbackStoreState.model_validate(state_payload)
        except (KeyError, TypeError, ValueError) as error:
            raise FeedbackStoreSafetyError() from error
        if not isinstance(digest, str) or digest != canonical_feedback_store_digest(state):
            raise FeedbackStoreSafetyError()
        return state, False

    def _read_state(self, *, migrate: bool) -> FeedbackStoreState:
        contents = self._read_bytes(self.path, max_bytes=self.max_bytes)
        if contents is None:
            return FeedbackStoreState()
        try:
            payload = json.loads(contents.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FeedbackStoreSafetyError() from error
        state, requires_migration = self._state_from_payload(payload)
        if requires_migration and migrate:
            self._atomic_write(state)
        return state

    def read(self) -> FeedbackStoreState:
        return self._read_state(migrate=True)

    def _validated_bundle(self, bundle: FeedbackBundle) -> FeedbackBundle:
        try:
            return FeedbackBundle.model_validate(bundle.model_dump(mode="json"))
        except (TypeError, ValueError) as error:
            raise FeedbackStoreSafetyError() from error

    def import_bundle(self, bundle: FeedbackBundle, *, dry_run: bool) -> FeedbackImportResult:
        checked_bundle = self._validated_bundle(bundle)
        previous = self._read_state(migrate=False)
        if checked_bundle.bundle_id in previous.applied_bundle_ids:
            return FeedbackImportResult(
                state=previous,
                applied_count=0,
                changed_count=0,
                duplicate_count=0,
                stale_count=0,
                conflict_count=0,
                bundle_duplicate_count=1,
            )
        merged = apply_feedback_commands(previous, checked_bundle.commands)
        bundle_ids = tuple(
            sorted({*merged.state.applied_bundle_ids, checked_bundle.bundle_id}, key=str)[
                -MAX_APPLIED_BUNDLE_IDS:
            ]
        )
        next_state = merged.state.model_copy(update={"applied_bundle_ids": bundle_ids})
        result = FeedbackImportResult(
            state=next_state,
            applied_count=merged.applied_count,
            changed_count=merged.changed_count,
            duplicate_count=merged.duplicate_count,
            stale_count=merged.stale_count,
            conflict_count=merged.conflict_count,
            bundle_duplicate_count=0,
        )
        if not dry_run:
            self._atomic_write(next_state)
        return result

    def _atomic_write(self, state: FeedbackStoreState) -> None:
        path = self._checked_path(self.path)
        try:
            self.file_ops.ensure_parent(str(path.parent))
        except (OSError, ValueError) as error:
            raise FeedbackStoreSafetyError() from error
        self._checked_path(path)
        payload = _canonical_json(
            {
                "schema_version": FEEDBACK_SCHEMA_VERSION,
                "state": state.model_dump(mode="json"),
                "digest": canonical_feedback_store_digest(state),
            }
        ).encode("utf-8")
        temp_name: str | None = None
        try:
            descriptor, temp_name = self.file_ops.make_temp(str(path.parent), ".feedback-", ".tmp")
            with self.file_ops.open_fd(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                self.file_ops.fsync(handle.fileno())
            self._checked_path(path)
            self.file_ops.replace(temp_name, str(path))
            temp_name = None
            self.file_ops.sync_directory(str(path.parent))
            self._checked_path(path)
        except (OSError, ValueError) as error:
            raise FeedbackStoreSafetyError() from error
        finally:
            if temp_name is not None:
                try:
                    self.file_ops.unlink(temp_name)
                except OSError:
                    pass


def load_feedback_bundle(
    path: Path | str,
    *,
    root: Path | str,
    max_bytes: int = MAX_FEEDBACK_BUNDLE_BYTES,
    file_ops: FeedbackStoreFileOps | None = None,
) -> FeedbackBundle:
    """Load a user-selected bundle through the same local boundary checks as the store."""
    boundary = FeedbackStore(path, root=root, max_bytes=max_bytes, file_ops=file_ops)
    contents = boundary._read_bytes(boundary.path, max_bytes=max_bytes)
    if contents is None:
        raise FeedbackStoreSafetyError()
    try:
        return FeedbackBundle.model_validate_json(contents)
    except (TypeError, ValueError) as error:
        raise FeedbackStoreSafetyError() from error


def _replace_record_for_command(record: FeedbackRecord, command: FeedbackCommand) -> tuple[FeedbackRecord, bool, bool]:
    watermark = command.watermark
    if command.action == "set_read":
        changed = record.read != command.value
        return (
            record.model_copy(update={"read": command.value, "read_watermark": watermark}),
            changed,
            False,
        )

    if command.action == "set_favorite":
        conflict = command.value and record.irrelevant
        favorite = command.value
        irrelevant = False if command.value else record.irrelevant
    else:
        conflict = command.value and record.favorite
        favorite = False if command.value else record.favorite
        irrelevant = command.value
    changed = (favorite, irrelevant) != (record.favorite, record.irrelevant)
    return (
        record.model_copy(
            update={
                "favorite": favorite,
                "irrelevant": irrelevant,
                "preference_watermark": watermark,
            }
        ),
        changed,
        conflict,
    )


def apply_feedback_commands(
    state: FeedbackStoreState, commands: Iterable[FeedbackCommand]
) -> FeedbackMergeResult:
    """Merge commands as a deterministic two-domain, last-writer-wins state machine."""
    records = {record.paper_id: record for record in state.records}
    applied_ids = set(state.applied_command_ids)
    applied_count = changed_count = duplicate_count = stale_count = conflict_count = 0

    for command in sorted(tuple(commands), key=lambda item: item.sort_key):
        if command.command_id in applied_ids:
            duplicate_count += 1
            continue
        record = records.get(command.paper_id, FeedbackRecord(paper_id=command.paper_id))
        watermark = (
            record.read_watermark if command.action == "set_read" else record.preference_watermark
        )
        if watermark is not None and command.sort_key <= watermark.sort_key:
            applied_ids.add(command.command_id)
            stale_count += 1
            continue
        replacement, changed, conflict = _replace_record_for_command(record, command)
        records[command.paper_id] = replacement
        applied_ids.add(command.command_id)
        applied_count += 1
        changed_count += int(changed)
        conflict_count += int(conflict)

    next_state = FeedbackStoreState(
        records=tuple(sorted(records.values(), key=lambda record: record.paper_id)),
        applied_command_ids=tuple(sorted(applied_ids, key=str))[:MAX_APPLIED_COMMAND_IDS],
        applied_bundle_ids=state.applied_bundle_ids,
    )
    return FeedbackMergeResult(
        state=next_state,
        applied_count=applied_count,
        changed_count=changed_count,
        duplicate_count=duplicate_count,
        stale_count=stale_count,
        conflict_count=conflict_count,
    )
