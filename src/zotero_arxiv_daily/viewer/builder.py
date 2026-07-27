from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from zotero_arxiv_daily.analysis.paper_schemas import PaperAnalysis
from zotero_arxiv_daily.analysis.validation_schemas import ValidationPaperResult
from zotero_arxiv_daily.viewer.assets import EvidenceImagePublisher, PublishedAsset
from zotero_arxiv_daily.viewer.filesystem import AtomicOutputRoot
from zotero_arxiv_daily.viewer.publication import PublicationPolicy
from zotero_arxiv_daily.viewer.renderer import TemplateRenderer
from zotero_arxiv_daily.viewer.schemas import BuildManifest, IndexPageModel, PaperPageModel, ViewerSettings


class StaticViewerBuilder:
    def __init__(
        self,
        settings: ViewerSettings,
        *,
        parallel_writes: bool = True,
        max_write_workers: int = 8,
        executor_factory: Callable[..., object] = ThreadPoolExecutor,
    ) -> None:
        self._settings = settings
        self._renderer = TemplateRenderer(site_title=settings.site_title)
        self._parallel_writes = parallel_writes
        self._max_write_workers = max_write_workers
        self._executor_factory = executor_factory

    def build(
        self,
        results: tuple[ValidationPaperResult, ...],
        *,
        batch_label: str,
        dry_run: bool = False,
    ) -> BuildManifest:
        output = AtomicOutputRoot(self._settings.output_root, dry_run=dry_run)
        policy = PublicationPolicy(allow_partial=self._settings.allow_partial)
        pages: list[PaperPageModel] = []
        written: list[str] = []
        entries: list[tuple[PurePosixPath, str]] = []
        partial_count = 0
        for result in results:
            decision = policy.decide(result)
            if decision.kind == "excluded" or result.validated is None or result.report is None:
                continue
            if len(pages) >= self._settings.max_papers:
                break
            analysis = result.validated.analysis
            filename = f"{analysis.paper_id.replace(':', '-')}.html"
            relative = PurePosixPath("papers", filename)
            page = PaperPageModel(
                paper_id=analysis.paper_id,
                relative_path=relative.as_posix(),
                english_title=analysis.english_title,
                chinese_title=analysis.chinese_title.text_zh if analysis.chinese_title else None,
                publication_kind=decision.kind,
            )
            entries.append(
                (
                    relative,
                    self._renderer.render_paper(
                    analysis,
                    result.report,
                    publication_kind=decision.kind,
                    evidence_image_urls=self._publish_evidence_images(analysis, dry_run=dry_run),
                    ),
                )
            )
            pages.append(page)
            written.append(relative.as_posix())
            partial_count += decision.kind == "partial"
        index = IndexPageModel(batch_label=batch_label, papers=tuple(pages), valid_count=len(pages) - partial_count, partial_count=partial_count)
        entries.extend(
            (
                (PurePosixPath("index.html"), self._renderer.render_index(index)),
                (PurePosixPath("assets", "site.css"), self._css_source()),
                (PurePosixPath("assets", "feedback.js"), self._feedback_source()),
                (PurePosixPath("assets", "favicon.svg"), self._favicon_source()),
            )
        )
        written.extend(("index.html", "assets/site.css", "assets/feedback.js", "assets/favicon.svg"))
        manifest = BuildManifest(
            build_version=self._settings.build_version,
            template_version=self._settings.template_version,
            published_count=len(pages),
            partial_count=partial_count,
            written_paths=tuple(sorted(written + ["build-manifest.json"])),
        )
        if self._parallel_writes:
            output.write_many_text(
                tuple(entries),
                max_workers=self._max_write_workers,
                executor_factory=self._executor_factory,
            )
        else:
            for relative, content in entries:
                output.write_text(relative, content)
        output.write_text(
            PurePosixPath("build-manifest.json"),
            manifest.model_dump_json(indent=2) + "\n",
        )
        output.remove_stale_files(
            PurePosixPath("papers"),
            suffix=".html",
            keep_names={Path(page.relative_path).name for page in pages},
        )
        return manifest

    @staticmethod
    def _css_source() -> str:
        return (Path(__file__).parent / "static" / "site.css").read_text(encoding="utf-8")

    @staticmethod
    def _favicon_source() -> str:
        return (Path(__file__).parent / "static" / "favicon.svg").read_text(encoding="utf-8")

    @staticmethod
    def _feedback_source() -> str:
        return (Path(__file__).parent / "static" / "feedback.js").read_text(encoding="utf-8")

    def _publish_evidence_images(self, analysis: PaperAnalysis, *, dry_run: bool) -> dict[str, str]:
        """Publish at most one approved local image per evidence visual."""
        publisher = EvidenceImagePublisher(
            self._settings.output_root,
            max_image_bytes=self._settings.max_image_bytes,
            dry_run=dry_run,
        )
        urls: dict[str, str] = {}
        published_count = 0
        for visual in analysis.supporting_visuals:
            if published_count >= self._settings.max_assets_per_paper:
                break
            for source in visual.image_paths:
                asset = self._publish_if_approved(publisher, source)
                if asset is not None:
                    urls[visual.evidence_id] = "../" + asset.relative_path.as_posix()
                    published_count += 1
                    break
        return urls

    def _publish_if_approved(
        self, publisher: EvidenceImagePublisher, source: Path
    ) -> PublishedAsset | None:
        for evidence_root in self._settings.evidence_roots:
            try:
                return publisher.publish(source, evidence_root=evidence_root)
            except (FileNotFoundError, ValueError):
                continue
        return None
