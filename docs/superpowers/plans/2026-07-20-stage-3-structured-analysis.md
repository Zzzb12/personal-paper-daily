# Stage 3 Structured Chinese Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a zero-cost-testable Stage 3 pipeline that converts at most five Stage 2 `DocumentGraph` values into strict, evidence-candidate-bound Chinese `PaperAnalysis` records.

**Architecture:** Deterministically project a bounded `EvidencePacket` from each `DocumentGraph`, send that packet through an injected structured-analysis client, parse a strict draft, materialize immutable Figure/Table provenance from the packet, and cache only successful results using complete version identity. Stage 3 enforces protocol/reference-set integrity; Stage 4 retains final semantic evidence validation and publication decisions.

**Tech Stack:** Python 3.13, Pydantic 2, OpenAI Python SDK 2.x, httpx, pytest, uv.

## Global Constraints

- Analyze content only from Stage 2 `DocumentGraph`; candidate metadata is limited to identity/title/links.
- Process at most `full_analysis_limit = 5` papers and retain at most 3 visual evidence records per paper.
- Never use real credentials, paid APIs, network, Zotero, email, Feishu, viewer, feedback, or GitHub Actions in tests or acceptance.
- Store missing facts as `None` or empty tuples; never invent Figure/Table labels, captions, parameters, settings, results, links, or limitations.
- Preserve PDF page, section, label, caption, bbox, image path, and `SourceMapping` for every visual projection.
- `system_inference` requires `inferred=true`; `author_statement` and `system_summary` require `inferred=false`.
- Cache identity includes PDF hash, DocumentGraph schema/parser/mapper/config/content versions, evidence builder/packet, prompt, analysis schema, model, and generation settings.
- Do not implement Stage 4 semantic evidence validation, rendering, delivery, feedback, or automation.
- Never print, persist, or inspect actual secret values; all acceptance clients are fakes.

---

### Task 1: Define strict Stage 3 schemas

**Files:**
- Create: `src/zotero_arxiv_daily/analysis/paper_schemas.py`
- Modify: `src/zotero_arxiv_daily/analysis/__init__.py`
- Create: `tests/analysis/test_paper_schemas.py`

**Interfaces:**
- Consumes: `BoundingBox`, `SourceMapping` from `analysis.document_schemas`.
- Produces: `EvidenceRegion`, `EvidenceCandidate`, `EvidencePacket`, `ClaimRecord`, `DraftSupportingVisual`, `MethodModule`, `ParameterRecord`, `AblationRecord`, `PaperAnalysisDraft`, `SupportingVisual`, `GenerationMetadata`, `PaperAnalysis`, `AnalysisIssue`, `PaperAnalysisResult`, `AnalysisBatchResult`.

- [ ] **Step 1: Write failing schema tests**

```python
def test_claim_source_requires_matching_inferred_flag():
    with pytest.raises(ValidationError, match="system_inference"):
        claim(source_type="system_inference", inferred=False)

def test_analysis_field_order_and_explicit_missing_values():
    data = analysis().model_dump()
    assert list(data)[:7] == [
        "schema_version", "paper_id", "english_title", "chinese_title",
        "recommendation_reason", "research_problem", "insights",
    ]
    assert data["parameters"] == ()
    assert data["limitations"] == ()

def test_visual_projection_preserves_stage_two_provenance():
    visual = supporting_visual()
    assert visual.regions[0].source_mapping.source_item_id == "docling-table"
    assert visual.regions[0].pdf_page == 2
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests/analysis/test_paper_schemas.py -q
```

Expected: collection fails because `paper_schemas` does not exist.

- [ ] **Step 3: Implement minimal strict schemas**

```python
ANALYSIS_SCHEMA_VERSION = "1.0"
EVIDENCE_PACKET_VERSION = "1.0"

class ClaimRecord(StrictModel):
    claim_id: str
    kind: Literal["recommendation", "problem", "insight", "insight_logic", "method", "difference", "result", "limitation", "support"]
    text_zh: str
    source_type: Literal["author_statement", "system_summary", "system_inference"]
    inferred: bool
    evidence_ids: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_inference(self) -> Self:
        expected = self.source_type == "system_inference"
        if self.inferred != expected:
            raise ValueError("system_inference requires inferred=true and other sources require false")
        return self
```

Use `extra="forbid"` through existing `StrictModel`, explicit optionals, stable IDs, URL strings, deterministic tuple fields, timezone-aware generation timestamps, finite confidence, and lowercase SHA-256 validation.

- [ ] **Step 4: Verify GREEN and regressions**

```powershell
uv run pytest tests/analysis/test_paper_schemas.py tests/analysis/test_document_schemas.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/analysis tests/analysis/test_paper_schemas.py
git commit -m "feat: define stage 3 analysis schemas"
```

### Task 2: Build deterministic bounded evidence packets

**Files:**
- Create: `src/zotero_arxiv_daily/documents/evidence.py`
- Create: `tests/documents/test_evidence.py`

**Interfaces:**
- Consumes: `DocumentGraph`, `DocumentBlock`, `SectionNode`, `VisualArtifact`.
- Produces: `EvidenceBuildSettings` and `build_evidence_packet(paper_id: str, document: DocumentGraph, settings: EvidenceBuildSettings) -> EvidencePacket`.

- [ ] **Step 1: Write failing evidence tests**

```python
def test_evidence_prioritizes_preferred_sections_and_marks_abstract():
    packet = build_evidence_packet("arxiv:2401.00001", document_graph(), settings())
    assert [item.section_title for item in packet.candidates[:3]] == [
        "1 Introduction", "3 Method", "4 Experiments"
    ]
    assert next(item for item in packet.candidates if item.section_title == "Abstract").abstract_only

def test_evidence_retains_at_most_three_visuals_with_all_regions():
    packet = build_evidence_packet("arxiv:2401.00001", graph_with_four_visuals(), settings())
    visuals = [item for item in packet.candidates if item.kind != "text"]
    assert len(visuals) == 3
    assert visuals[0].regions[0].source_mapping.parser == "docling"

def test_evidence_budget_is_deterministic_and_unicode_safe():
    first = build_evidence_packet("arxiv:2401.00001", long_chinese_graph(), settings(max_chars=120))
    second = build_evidence_packet("arxiv:2401.00001", long_chinese_graph(), settings(max_chars=120))
    assert first == second
    assert len("".join(item.evidence_text or "" for item in first.candidates)) <= 120
```

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest tests/documents/test_evidence.py -q
```

Expected: import fails because `documents.evidence` does not exist.

- [ ] **Step 3: Implement minimal builder**

```python
class EvidenceBuildSettings(StrictModel):
    max_candidates: int = Field(default=48, ge=1, le=200)
    max_chars: int = Field(default=24_000, ge=256)
    max_block_chars: int = Field(default=2_000, ge=64)
    max_visuals: int = Field(default=3, ge=0, le=3)
    builder_version: str = "1"

def build_evidence_packet(
    paper_id: str,
    document: DocumentGraph,
    settings: EvidenceBuildSettings,
) -> EvidencePacket:
    sections = _section_paths(document.sections)
    candidates = _text_candidates(paper_id, document, sections, settings)
    candidates += _visual_candidates(paper_id, document, sections)[: settings.max_visuals]
    bounded = _apply_budget(sorted(candidates, key=_candidate_order), settings)
    return EvidencePacket.from_candidates(paper_id, document, settings, bounded)
```

IDs are SHA-256-derived from PDF hash plus source IDs/page/bbox/type. Never infer a missing label, caption, bbox, section, image path, or source mapping.

- [ ] **Step 4: Verify GREEN and Stage 2 mapper regression**

```powershell
uv run pytest tests/documents/test_evidence.py tests/documents/test_mapper.py tests/analysis/test_document_schemas.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/documents/evidence.py tests/documents/test_evidence.py
git commit -m "feat: build bounded document evidence packets"
```

### Task 3: Define versioned prompt and injected client

**Files:**
- Create: `src/zotero_arxiv_daily/analysis/prompts/__init__.py`
- Create: `src/zotero_arxiv_daily/analysis/prompts/stage3_v1.py`
- Create: `src/zotero_arxiv_daily/analysis/client.py`
- Create: `tests/analysis/test_prompt.py`
- Create: `tests/analysis/test_client.py`

**Interfaces:**
- Consumes: `CandidatePaper`, `EvidencePacket`, `PaperAnalysisDraft.model_json_schema()`.
- Produces: `AnalysisRequest`, `StructuredAnalysisClient`, `AnalysisClientError`, `OpenAICompatibleAnalysisClient`, `build_analysis_request(paper: CandidatePaper, packet: EvidencePacket, max_output_tokens: int) -> AnalysisRequest`.

- [ ] **Step 1: Write failing prompt/client tests**

```python
def test_prompt_is_versioned_minimized_and_forbids_fabrication():
    request = build_analysis_request(candidate(), packet(), max_output_tokens=4000)
    assert request.prompt_version == "stage3-v1"
    assert "只能引用" in request.system_prompt
    assert "ZOTERO_KEY" not in request.user_prompt
    assert "collection_paths" not in request.user_prompt

def test_client_rejects_oversized_response_without_logging_body():
    adapter = OpenAICompatibleAnalysisClient(
        sdk_client=fake_sdk_response("x" * 101), model="fake", response_max_bytes=100
    )
    with pytest.raises(AnalysisClientError, match="analysis_response_too_large"):
        adapter.generate(request())
```

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest tests/analysis/test_prompt.py tests/analysis/test_client.py -q
```

Expected: prompt/client imports fail.

- [ ] **Step 3: Implement minimal prompt and adapter**

```python
PROMPT_VERSION = "stage3-v1"

class StructuredAnalysisClient(Protocol):
    model_identity: str
    def generate(self, request: AnalysisRequest) -> str:
        raise NotImplementedError

class AnalysisClientError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, retry_after_seconds: float | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
```

The production adapter accepts an already-built SDK client for unit testing, sets SDK retries to zero in its factory, uses explicit timeout, requests JSON output, enforces UTF-8 response byte length, and translates errors without including prompt, response, key, base URL, or private text.

- [ ] **Step 4: Verify GREEN**

```powershell
uv run pytest tests/analysis/test_prompt.py tests/analysis/test_client.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/analysis/prompts src/zotero_arxiv_daily/analysis/client.py tests/analysis/test_prompt.py tests/analysis/test_client.py
git commit -m "feat: define structured analysis prompt client"
```

### Task 4: Implement complete-identity analysis cache

**Files:**
- Create: `src/zotero_arxiv_daily/analysis/cache.py`
- Create: `tests/analysis/test_analysis_cache.py`

**Interfaces:**
- Consumes: `CandidatePaper`, `DocumentGraph`, `EvidencePacket`, prompt/schema/model/generation identities, `PaperAnalysis`.
- Produces: `AnalysisCacheIdentity`, `AnalysisCache`, `build_analysis_cache_identity(paper: CandidatePaper, document: DocumentGraph, packet: EvidencePacket, prompt_version: str, analysis_schema_version: str, model_identity: str, generation_identity: str) -> AnalysisCacheIdentity`.

- [ ] **Step 1: Write failing cache tests**

```python
@pytest.mark.parametrize("field", [
    "pdf_sha256", "document_schema_version", "parser_version", "mapper_version",
    "document_config_version", "content_fingerprint", "packet_fingerprint",
    "prompt_version", "analysis_schema_version", "model_identity", "generation_identity",
])
def test_cache_key_changes_for_every_required_identity(field):
    assert identity().cache_key != identity(**{field: changed_value(field)}).cache_key

def test_cache_rejects_corrupt_or_external_entries(tmp_path):
    cache = AnalysisCache(tmp_path / "cache")
    cache.path_for(identity()).write_text("not json", encoding="utf-8")
    assert cache.read(identity()) is None
```

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest tests/analysis/test_analysis_cache.py -q
```

- [ ] **Step 3: Implement minimal cache**

```python
class AnalysisCache:
    def read(self, identity: AnalysisCacheIdentity) -> PaperAnalysis | None:
        path = self.path_for(identity)
        try:
            envelope = AnalysisCacheEnvelope.model_validate_json(path.read_text("utf-8"))
        except (OSError, ValueError, ValidationError):
            return None
        return envelope.analysis if envelope.identity == identity else None

    def write(self, identity: AnalysisCacheIdentity, analysis: PaperAnalysis) -> Path:
        target = self.path_for(identity)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = AnalysisCacheEnvelope(identity=identity, analysis=analysis).model_dump_json(indent=2) + "\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            checked = AnalysisCacheEnvelope.model_validate_json(
                temporary.read_text(encoding="utf-8")
            )
            if checked.identity != identity or checked.analysis != analysis:
                raise ValueError("analysis cache revalidation failed")
            os.replace(temporary, target)
            temporary = None
            return target
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
```

Use this explicit atomic-write sequence and keep the completed source free of placeholders.

- [ ] **Step 4: Verify GREEN and store regressions**

```powershell
uv run pytest tests/analysis/test_analysis_cache.py tests/candidates/test_store.py tests/documents/test_cache.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/analysis/cache.py tests/analysis/test_analysis_cache.py
git commit -m "feat: cache structured paper analyses"
```

### Task 5: Implement retrying analyzer and provenance materialization

**Files:**
- Create: `src/zotero_arxiv_daily/analysis/analyzer.py`
- Create: `tests/analysis/test_analyzer.py`

**Interfaces:**
- Consumes: `CandidatePaper`, `DocumentGraph`, `EvidencePacket`, `StructuredAnalysisClient`, `AnalysisCache`.
- Produces: `AnalysisSettings`, `AnalysisDependencies`, `analyze_paper(paper: CandidatePaper, document: DocumentGraph, settings: AnalysisSettings, dependencies: AnalysisDependencies) -> PaperAnalysisResult`.

- [ ] **Step 1: Write failing analyzer tests**

```python
def test_analyzer_materializes_visual_provenance_from_packet_not_llm():
    result = analyze_paper(candidate(), graph(), settings(), dependencies(valid_draft()))
    visual = result.analysis.supporting_visuals[0]
    assert visual.label == "Table 1"
    assert visual.regions[0].source_mapping.source_item_id == "docling-table"

def test_analyzer_rejects_unknown_and_abstract_only_insight_evidence():
    assert analyze_paper(candidate(), graph(), settings(), dependencies(unknown_id_draft())).status == "failed"
    assert analyze_paper(candidate(), graph(), settings(), dependencies(abstract_only_draft())).issues[0].code == "analysis_abstract_only_insight"

def test_identical_second_analysis_is_cache_hit_with_zero_client_calls():
    deps = dependencies(valid_draft())
    first = analyze_paper(candidate(), graph(), settings(), deps)
    second = analyze_paper(candidate(), graph(), settings(), deps)
    assert first.status == second.status == "success"
    assert second.cache_hit is True
    assert deps.client.calls == 1
```

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest tests/analysis/test_analyzer.py -q
```

- [ ] **Step 3: Implement minimal analyzer**

```python
def analyze_paper(
    paper: CandidatePaper,
    document: DocumentGraph,
    settings: AnalysisSettings,
    dependencies: AnalysisDependencies,
) -> PaperAnalysisResult:
    packet = build_evidence_packet(paper.paper_id, document, settings.evidence)
    identity = build_analysis_cache_identity(paper, document, packet, settings, dependencies.client)
    if cached := dependencies.cache.read(identity):
        return PaperAnalysisResult.success(cached, cache_hit=True)
    raw = _generate_with_bounded_retry(dependencies.client, build_analysis_request(paper, packet, settings), settings)
    draft = PaperAnalysisDraft.model_validate_json(raw)
    analysis = _materialize_and_check(paper, document, packet, draft, identity, dependencies.clock())
    dependencies.cache.write(identity, analysis)
    return PaperAnalysisResult.success(analysis, cache_hit=False)
```

Catch and translate malformed JSON, Schema failures, retry exhaustion, permanent errors, unknown evidence, provenance mismatch, response size, and empty packet without returning raw content in messages. Cache only success.

- [ ] **Step 4: Verify GREEN**

```powershell
uv run pytest tests/analysis/test_analyzer.py tests/analysis/test_prompt.py tests/analysis/test_analysis_cache.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/analysis/analyzer.py tests/analysis/test_analyzer.py
git commit -m "feat: analyze papers with bounded structured calls"
```

### Task 6: Compose the Stage 3 batch pipeline and offline dry-run

**Files:**
- Create: `src/zotero_arxiv_daily/pipeline/analysis.py`
- Modify: `src/zotero_arxiv_daily/pipeline/__init__.py`
- Modify: `config/base.yaml`
- Create: `tests/pipeline/test_analysis.py`
- Create: `tests/fixtures/stage3_offline.json`
- Create: `tests/analysis/test_stage3_offline.py`

**Interfaces:**
- Consumes: `CandidateBatch`, `DocumentBatchResult`, `AnalysisSettings`, injected analyzer dependencies.
- Produces: `build_analysis_batch(candidates: CandidateBatch, documents: DocumentBatchResult, settings: AnalysisSettings, dependencies: AnalysisDependencies) -> AnalysisBatchResult` and `python -m zotero_arxiv_daily.pipeline.analysis --dry-run --offline-fixture PATH`.

- [ ] **Step 1: Write failing pipeline and CLI tests**

```python
def test_pipeline_analyzes_only_selected_documents_in_order_and_at_most_five():
    dependencies = deps()
    result = build_analysis_batch(candidate_batch(8), document_batch(8), settings(), dependencies)
    assert [item.paper_id for item in result.results] == selected_ids()[:5]
    assert dependencies.client.calls <= 5

def test_pipeline_isolates_missing_failed_and_mismatched_documents():
    result = build_analysis_batch(batch(), mixed_document_batch(), settings(), deps())
    assert [item.status for item in result.results] == ["success", "skipped", "failed"]

def test_offline_dry_run_does_not_read_credentials_call_network_or_write_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    result = run_offline_fixture(FIXTURE, dry_run=True, cache_root=tmp_path / "cache")
    assert result.expensive_call_count == 0
    assert not (tmp_path / "cache").exists()
```

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest tests/pipeline/test_analysis.py tests/analysis/test_stage3_offline.py -q
```

- [ ] **Step 3: Implement pipeline, config, fixture, and dry-run**

```python
def build_analysis_batch(
    candidates: CandidateBatch,
    documents: DocumentBatchResult,
    settings: AnalysisSettings,
    dependencies: AnalysisDependencies,
) -> AnalysisBatchResult:
    selected = candidates.selected_for_full_analysis[:5]
    by_id = {item.paper_id: item for item in documents.results}
    results = tuple(_analyze_selected(paper_id, candidates, by_id, settings, dependencies) for paper_id in selected)
    return AnalysisBatchResult(run_id=candidates.run_id, created_at=dependencies.clock(), results=results)
```

The offline fixture contains only artificial candidate/document JSON and a fake structured response. Dry-run parses and validates request/draft/materialization without constructing the production client or writing cache.

Add these exact non-secret defaults; do not put credential references or values into YAML:

```yaml
analysis_pipeline:
  cache_root: cache/analysis
  prompt_version: stage3-v1
  schema_version: "1.0"
  config_version: "1"
  max_papers: 5
  max_visuals_per_paper: 3
  max_evidence_candidates: 48
  max_evidence_chars: 24000
  max_block_chars: 2000
  max_output_tokens: 8192
  response_max_bytes: 1048576
  request_timeout: {connect: 10, read: 60, write: 10, pool: 10}
  retry: {max_attempts: 3, backoff_seconds: 1, max_retry_after_seconds: 60}
```

- [ ] **Step 4: Verify GREEN and prior-stage regressions**

```powershell
uv run pytest tests/pipeline/test_analysis.py tests/analysis/test_stage3_offline.py -q
uv run pytest tests/analysis/test_document_schemas.py tests/documents tests/pipeline/test_documents.py -q
uv run pytest tests/analysis/test_stage1_schemas.py tests/interest tests/candidates tests/retriever/test_arxiv_metadata.py tests/pipeline/test_candidates.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/pipeline config/base.yaml tests/pipeline/test_analysis.py tests/fixtures/stage3_offline.json tests/analysis/test_stage3_offline.py
git commit -m "feat: build offline stage 3 analysis pipeline"
```

### Task 7: Verify, review, document, and finish Stage 3

**Files:**
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/BASELINE.md`

**Interfaces:**
- Consumes: all Stage 3 implementation and fresh command output.
- Produces: factual completion documentation and a review-ready branch.

- [ ] **Step 1: Run fresh layered verification**

```powershell
uv sync --frozen
uv run pytest tests/analysis/test_paper_schemas.py tests/analysis/test_prompt.py tests/analysis/test_client.py tests/analysis/test_analysis_cache.py tests/analysis/test_analyzer.py tests/analysis/test_stage3_offline.py tests/documents/test_evidence.py tests/pipeline/test_analysis.py -q
uv run pytest tests/analysis/test_document_schemas.py tests/documents tests/pipeline/test_documents.py -q
uv run pytest tests/analysis/test_stage1_schemas.py tests/interest tests/candidates tests/retriever/test_arxiv_metadata.py tests/pipeline/test_candidates.py -q
uv run pytest -q
uv run pytest -m "slow or not slow" -q
uv run python -m compileall -q src
```

Record exact results. Do not hide the two known Windows spawn failures or the Hugging Face slow model/cache failure.

- [ ] **Step 2: Run repository hygiene and secret scans**

```powershell
git status --short
git diff --check
git ls-files "*.pdf" "*.zip" ".env" ".env.*" "data/zotero/**" "cache/**"
git diff --numstat b2ad648..HEAD
```

Scan tracked text for common token/private-key shapes while reporting only rule counts and filenames, never matched values. Confirm ignored behavior for `.env`, analysis caches, PDFs, Zotero data, and temporary outputs; confirm `.env.example` remains trackable.

- [ ] **Step 3: Request independent code review**

Use `superpowers:requesting-code-review` against `b2ad648..HEAD`. Review Stage boundary, DocumentGraph-only content, Abstract handling, reference/provenance copying, cache identity, retry/timeout/response limits, dry-run zero-network behavior, secret safety, per-paper isolation, and test effectiveness. Fix every confirmed Critical/Important through a new RED→GREEN cycle.

- [ ] **Step 4: Update roadmap and baseline from fresh evidence**

Mark Stage 3 completed only after Stage 3 tests pass, known baseline failures remain classified, security scans pass, and final review says Ready. Document exact versions, commands, counts, zero-cost acceptance, known limits, rollback, and that Stage 4 has not begun.

- [ ] **Step 5: Final commits and branch handoff**

```powershell
git add docs/IMPLEMENTATION_PLAN.md docs/BASELINE.md
git commit -m "docs: record stage 3 verification"
git status --short
git log --oneline -12
```

Use `superpowers:verification-before-completion` and then `superpowers:finishing-a-development-branch`. Do not create a PR, do not push upstream, and preserve the Stage 3 branch/worktree unless the user later chooses integration.
