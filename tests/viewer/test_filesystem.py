from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_arxiv_daily.viewer.filesystem import AtomicOutputRoot


def test_writes_a_relative_text_file_beneath_output_root(tmp_path: Path) -> None:
    output = AtomicOutputRoot(tmp_path / "site")

    path = output.write_text(PurePosixPath("papers", "paper.html"), "<main>阅读</main>")

    assert path == tmp_path / "site" / "papers" / "paper.html"
    assert path.read_text(encoding="utf-8") == "<main>阅读</main>"


@pytest.mark.parametrize("relative", [PurePosixPath("..", "escape.html"), PurePosixPath("/absolute.html")])
def test_rejects_output_path_escape(tmp_path: Path, relative: PurePosixPath) -> None:
    with pytest.raises(ValueError, match="relative"):
        AtomicOutputRoot(tmp_path / "site").write_text(relative, "blocked")


def test_dry_run_creates_no_output(tmp_path: Path) -> None:
    path = AtomicOutputRoot(tmp_path / "site", dry_run=True).write_text(PurePosixPath("index.html"), "x")

    assert path == tmp_path / "site" / "index.html"
    assert not (tmp_path / "site").exists()


def test_rejects_existing_symlink_inside_output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "site"
    link = root / "papers"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda value: value == link or original(value))

    with pytest.raises(ValueError, match="symbolic link"):
        AtomicOutputRoot(root).write_text(PurePosixPath("papers", "paper.html"), "blocked")


def test_rejects_symlink_or_junction_ancestor_of_output_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ancestor = tmp_path / "linked"
    root = ancestor / "nested" / "site"
    original_symlink = Path.is_symlink
    original_junction = getattr(Path, "is_junction", lambda value: False)
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda value: value == ancestor or original_symlink(value),
    )

    with pytest.raises(ValueError, match="link|reparse"):
        AtomicOutputRoot(root).write_text(PurePosixPath("index.html"), "blocked")

    monkeypatch.setattr(Path, "is_symlink", original_symlink)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda value: value == ancestor or original_junction(value),
        raising=False,
    )
    with pytest.raises(ValueError, match="link|reparse"):
        AtomicOutputRoot(root).write_text(PurePosixPath("index.html"), "blocked")


def test_rejects_other_reparse_point_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ancestor = tmp_path / "reparse"
    root = ancestor / "site"
    original_lstat = Path.lstat
    monkeypatch.setattr(Path, "is_symlink", lambda value: False)
    monkeypatch.setattr(Path, "is_junction", lambda value: False, raising=False)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda value: (
            SimpleNamespace(st_file_attributes=0x400)
            if value == ancestor
            else original_lstat(value)
        ),
    )

    with pytest.raises(ValueError, match="link|reparse"):
        AtomicOutputRoot(root).write_text(PurePosixPath("index.html"), "blocked")


@pytest.mark.parametrize("unsafe_name", (r"\\Windows\\Temp\\escape.html", r"C:\\escape.html", r"C:escape.html"))
def test_rejects_windows_absolute_or_drive_qualified_output_component(
    tmp_path: Path, unsafe_name: str
) -> None:
    output = AtomicOutputRoot(tmp_path / "site", dry_run=True)

    with pytest.raises(ValueError, match="Windows path"):
        output.write_text(PurePosixPath("papers", unsafe_name), "unsafe")


def test_batch_write_preserves_order_and_matches_sequential_bytes(
    tmp_path: Path,
) -> None:
    entries = (
        (PurePosixPath("papers", "one.html"), "一\n"),
        (PurePosixPath("papers", "two.html"), "two\n"),
        (PurePosixPath("assets", "site.css"), "body{}\n"),
    )
    sequential = AtomicOutputRoot(tmp_path / "sequential")
    for relative, content in entries:
        sequential.write_text(relative, content)

    written = AtomicOutputRoot(tmp_path / "parallel").write_many_text(
        entries,
        max_workers=2,
    )

    assert written == tuple(
        tmp_path / "parallel" / Path(*relative.parts) for relative, _ in entries
    )
    for relative, _ in entries:
        assert (tmp_path / "parallel" / Path(*relative.parts)).read_bytes() == (
            tmp_path / "sequential" / Path(*relative.parts)
        ).read_bytes()


def test_batch_prevalidates_every_target_before_creating_executor(
    tmp_path: Path,
) -> None:
    executor_factory = Mock(side_effect=AssertionError("must not construct"))
    output = AtomicOutputRoot(tmp_path / "site")

    with pytest.raises(ValueError, match="duplicate"):
        output.write_many_text(
            (
                (PurePosixPath("index.html"), "one"),
                (PurePosixPath("index.html"), "two"),
            ),
            executor_factory=executor_factory,
        )
    with pytest.raises(ValueError, match="relative"):
        output.write_many_text(
            (
                (PurePosixPath("index.html"), "one"),
                (PurePosixPath("..", "escape.html"), "two"),
            ),
            executor_factory=executor_factory,
        )

    executor_factory.assert_not_called()
    assert not (tmp_path / "site").exists()


@pytest.mark.parametrize("workers", (0, 9, True))
def test_batch_rejects_unbounded_worker_counts(
    tmp_path: Path, workers: object
) -> None:
    with pytest.raises(ValueError, match="max_workers"):
        AtomicOutputRoot(tmp_path / "site").write_many_text(
            ((PurePosixPath("index.html"), "x"),),
            max_workers=workers,
        )


def test_batch_joins_all_submitted_futures_and_raises_fixed_error(
    tmp_path: Path,
) -> None:
    joined: list[str] = []

    class Future:
        def __init__(self, label: str, *, fail: bool = False) -> None:
            self.label = label
            self.fail = fail

        def result(self):
            joined.append(self.label)
            if self.fail:
                raise RuntimeError("PRIVATE WORKER DETAIL")
            return tmp_path / self.label

    class Executor:
        def __init__(self, *, max_workers: int) -> None:
            assert max_workers == 2
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, callback, target, content):
            self.calls += 1
            return Future(target.name, fail=self.calls == 1)

    with pytest.raises(RuntimeError, match="batch write failed") as captured:
        AtomicOutputRoot(tmp_path / "site").write_many_text(
            (
                (PurePosixPath("one.html"), "one"),
                (PurePosixPath("two.html"), "two"),
            ),
            max_workers=2,
            executor_factory=Executor,
        )

    assert joined == ["one.html", "two.html"]
    assert "PRIVATE" not in str(captured.value)


def test_empty_and_dry_run_batch_create_no_executor_or_output(
    tmp_path: Path,
) -> None:
    executor_factory = Mock(side_effect=AssertionError("must not construct"))
    output = AtomicOutputRoot(tmp_path / "site", dry_run=True)

    assert output.write_many_text((), executor_factory=executor_factory) == ()
    paths = output.write_many_text(
        ((PurePosixPath("index.html"), "x"),),
        executor_factory=executor_factory,
    )

    assert paths == (tmp_path / "site" / "index.html",)
    assert not (tmp_path / "site").exists()
    executor_factory.assert_not_called()
