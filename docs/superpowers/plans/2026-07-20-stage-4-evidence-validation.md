# Stage 4 Evidence Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, offline Stage 4 validator that proves Stage 3 claims, visuals, parameters, and ablations resolve unchanged to Stage 2/3 evidence and blocks non-eligible analyses from publication.

**Architecture:** Strict Stage 4 schemas represent issues, validation state, and publication eligibility. A pure validator traverses CandidatePaper, DocumentGraph, EvidencePacket, and PaperAnalysis without rewriting them; a complete-identity cache stores deterministic validation results; a batch pipeline isolates failures and exposes an offline golden CLI.

**Tech Stack:** Python 3.13, Pydantic 2.12, pytest 9, uv, standard-library hashlib/json/pathlib/tempfile.

## Global Constraints

- Start from Stage 3 commit `8603098474db25826de88af407bdfffb00cdb2b7` on `feat/stage-4-evidence-validation` in `.worktrees/stage-4-evidence-validation`.
- Process at most five papers and retain the existing Stage 3 maximum of three SupportingVisual records per paper.
- Analysis content remains DocumentGraph-only; CandidatePaper supplies only paper identity, English title, and PDF/arXiv/code links to validation.
- Never mutate or reconstruct Stage 3 evidence candidates or overwrite Figure/Table provenance.
- Never accept Abstract-only evidence as support for a successful Insight.
- Missing optional parameter, ablation, limitation, or code-link facts remain `None`/empty tuples and are not hallucination issues.
- `VALIDATION_SCHEMA_VERSION = "1.0"`; initial `VALIDATOR_VERSION = "stage4-v1"`; validator version participates in cache and run identity.
- Only `valid` analyses are publication-eligible; `partial` and `invalid` are blocked.
- No corrective LLM retry, UI, delivery, feedback, Actions, parser/OCR/VLM work, real credentials, real network, paid call, PDF/model download, PR, or upstream push.
- Every behavior change follows RED -> minimal implementation -> GREEN -> relevant regression -> commit. Never skip, delete, relax, or mock around the core validator.

---

### Task 1: Define strict Stage 4 validation schemas

**Files:**
- Create: `src/zotero_arxiv_daily/analysis/validation_schemas.py`
- Modify: `src/zotero_arxiv_daily/analysis/__init__.py`
- Create: `tests/analysis/test_validation_schemas.py`

**Interfaces:**
- Consumes: `StrictModel`, `PaperAnalysis`.
- Produces: `VALIDATION_SCHEMA_VERSION`, `VALIDATOR_VERSION`, `ValidationIssue`, `ClaimValidationResult`, `ValidationReport`, `ValidatedPaperAnalysis`, `ValidationPaperResult`, `ValidationBatchResult`.

- [ ] **Step 1: Write failing schema tests**

```python
def test_only_valid_report_is_publication_eligible():
    report = report_factory(status="valid", issues=())
    assert report.publication_eligibility == "eligible"
    with pytest.raises(ValidationError, match="blocked"):
        report_factory(status="invalid", publication_eligibility="eligible")

def test_validation_issue_is_safe_and_locatable():
    issue = ValidationIssue(
        code="unknown_evidence", severity="error", paper_id="arxiv:2401.00001",
        field_path="insights.0.evidence_ids.0", evidence_id="missing",
        message="Referenced evidence does not exist",
    )
    assert issue.claim_id is None
    with pytest.raises(ValidationError, match="location"):
        ValidationIssue(code="x", severity="error", paper_id="p", message="safe")

def test_validation_batch_is_versioned_unique_and_capped_at_five():
    batch = ValidationBatchResult(run_id="run-1", created_at=NOW, results=())
    assert batch.validator_version == "stage4-v1"
    with pytest.raises(ValidationError, match="at most five"):
        ValidationBatchResult(run_id="run-1", created_at=NOW, results=six_results())
```

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/analysis/test_validation_schemas.py -q
```

Expected: collection fails because `analysis.validation_schemas` does not exist.

- [ ] **Step 3: Implement the minimal strict models**

```python
VALIDATION_SCHEMA_VERSION = "1.0"
VALIDATOR_VERSION = "stage4-v1"

class ValidationIssue(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    paper_id: str
    claim_id: str | None = None
    field_path: str | None = None
    evidence_id: str | None = None
    visual_id: str | None = None
    message: str

class ClaimValidationResult(StrictModel):
    claim_id: str
    status: Literal["valid", "invalid"]
    resolved_evidence_ids: tuple[str, ...]
    issue_codes: tuple[str, ...] = ()

class ValidationReport(StrictModel):
    schema_version: Literal["1.0"] = VALIDATION_SCHEMA_VERSION
    validator_version: str = VALIDATOR_VERSION
    paper_id: str
    status: Literal["valid", "partial", "invalid"]
    publication_eligibility: Literal["eligible", "blocked"]
    input_fingerprint: str
    claim_results: tuple[ClaimValidationResult, ...]
    issues: tuple[ValidationIssue, ...]

    @model_validator(mode="after")
    def validate_eligibility(self) -> Self:
        expected = "eligible" if self.status == "valid" and not any(
            item.severity == "error" for item in self.issues
        ) else "blocked"
        if self.publication_eligibility != expected:
            raise ValueError(f"publication eligibility must be {expected}")
        return self
```

Add the immutable analysis wrapper, per-paper result state invariants, timezone-aware batch timestamp, unique paper IDs, maximum five results, and validator-version equality across batch/results. Normalize identifiers/messages but never include source text in validation messages.

- [ ] **Step 4: Run GREEN and Stage 3 schema regression**

```powershell
uv run pytest tests/analysis/test_validation_schemas.py tests/analysis/test_paper_schemas.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/analysis tests/analysis/test_validation_schemas.py
git commit -m "feat: define stage 4 validation schemas"
```

### Task 2: Validate immutable input identity and evidence provenance

**Files:**
- Create: `src/zotero_arxiv_daily/analysis/validator.py`
- Create: `tests/analysis/stage4_factories.py`
- Create: `tests/analysis/test_validator_identity.py`

**Interfaces:**
- Consumes: `CandidatePaper`, `DocumentGraph`, `EvidencePacket`, `PaperAnalysisResult`.
- Produces: `validate_paper(candidate, document, packet, analysis_result, *, validator_version=VALIDATOR_VERSION) -> ValidationPaperResult` and `validation_input_fingerprint(...) -> str`.

- [ ] **Step 1: Create artificial object factories and failing golden/identity tests**

```python
def test_valid_golden_preserves_original_analysis_object():
    inputs = golden_inputs()
    result = validate_paper(*inputs)
    assert result.status == "validated"
    assert result.validated.report.status == "valid"
    assert result.validated.report.publication_eligibility == "eligible"
    assert result.validated.analysis is inputs.analysis_result.analysis

@pytest.mark.parametrize("mutation,code", [
    (mutate_analysis_title, "candidate_title_mismatch"),
    (mutate_pdf_link, "candidate_link_mismatch"),
    (mutate_arxiv_link, "candidate_link_mismatch"),
    (mutate_code_link, "candidate_link_mismatch"),
    (mutate_saved_evidence_candidate, "analysis_evidence_candidates_modified"),
])
def test_candidate_and_saved_evidence_tampering_is_invalid(mutation, code):
    result = validate_paper(*mutation(golden_inputs()))
    assert result.status == "invalid"
    assert code in issue_codes(result)
```

Add explicit negative cases for packet/document fingerprint mismatch and paper ID mismatch.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/analysis/test_validator_identity.py -q
```

Expected: import fails because `analysis.validator` does not exist.

- [ ] **Step 3: Implement identity checks and deterministic issue collection**

```python
def validate_paper(candidate, document, packet, analysis_result, *, validator_version=VALIDATOR_VERSION):
    started = time.perf_counter()
    if analysis_result.status in {"failed", "skipped"} or analysis_result.analysis is None:
        return _unavailable_result(analysis_result, started, validator_version)
    analysis = analysis_result.analysis
    issues: list[ValidationIssue] = []
    _validate_identities(candidate, document, packet, analysis_result, analysis, issues)
    _validate_packet_against_document(document, packet, issues)
    _validate_analysis_candidates(packet, analysis, issues)
    return _finish_validation(analysis_result, analysis, issues, started, validator_version)
```

Use fixed safe messages selected by error code. Compute fingerprints from canonical `model_dump(mode="json")` JSON with sorted keys and SHA-256. Compare analysis evidence candidates to packet candidates by exact tuple equality. For Figure/Table candidates, compare visual ID, kind, label, caption, page, section ID/title/path, every bbox/image path/SourceMapping/confidence, and candidate confidence to the indexed DocumentGraph visual and Section tree. For text candidates, resolve block IDs, pages, sections, section paths, and SourceMapping without requiring truncated evidence text to equal the full block text.

- [ ] **Step 4: Add and run provenance-tampering RED cases one field at a time**

```python
@pytest.mark.parametrize("field", [
    "label", "caption", "pdf_page", "bbox", "section_id", "section_title",
    "section_path", "source_mapping", "image_path", "confidence",
])
def test_visual_provenance_field_tampering_is_invalid(field):
    result = validate_paper(*mutate_visual_provenance(golden_inputs(), field))
    assert result.status == "invalid"
    assert "visual_provenance_mismatch" in issue_codes(result)
```

Run each newly added case before its comparison exists and save the genuine failure in the task notes/terminal history; then add only the missing comparison.

- [ ] **Step 5: Run GREEN and Stage 2/3 provenance regressions**

```powershell
uv run pytest tests/analysis/test_validator_identity.py tests/documents/test_evidence.py tests/analysis/test_document_schemas.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add src/zotero_arxiv_daily/analysis/validator.py tests/analysis/stage4_factories.py tests/analysis/test_validator_identity.py
git commit -m "feat: validate evidence identity and provenance"
```

### Task 3: Validate claims, visuals, parameters, ablations, and status

**Files:**
- Modify: `src/zotero_arxiv_daily/analysis/validator.py`
- Create: `tests/analysis/test_validator_rules.py`

**Interfaces:**
- Extends `validate_paper(...)` from Task 2.
- Produces complete `ClaimValidationResult` values and final `valid/partial/invalid` classification.

- [ ] **Step 1: Write failing claim/reference tests**

```python
def test_unknown_evidence_id_is_invalid():
    result = validate_paper(*with_claim_evidence(golden_inputs(), "unknown"))
    assert result.status == "invalid"
    assert "unknown_evidence" in issue_codes(result)

def test_abstract_only_insight_is_invalid():
    result = validate_paper(*with_abstract_only_insight(golden_inputs()))
    assert result.status == "invalid"
    assert "abstract_only_insight" in issue_codes(result)

@pytest.mark.parametrize("builder,code", [
    (with_duplicate_claim_id, "duplicate_claim_id"),
    (with_wrong_claim_kind, "claim_kind_mismatch"),
    (with_inference_flag_mismatch, "claim_source_inference_mismatch"),
])
def test_claim_structure_corruption_is_invalid(builder, code):
    result = validate_paper(*builder(golden_inputs()))
    assert result.status == "invalid"
    assert code in issue_codes(result)
```

Use `model_construct` only to create deliberately corrupt objects that normal Stage 3 Pydantic construction rejects; the validator itself remains real and unmocked.

- [ ] **Step 2: Run RED and implement claim traversal**

Run:

```powershell
uv run pytest tests/analysis/test_validator_rules.py -k "unknown or abstract or claim" -q
```

Implement `_iter_claim_locations`, whole-analysis claim ID uniqueness, expected kind by field, source/inferred truth table, evidence existence, and non-Abstract Insight support. Return a `ClaimValidationResult` for every traversed claim.

- [ ] **Step 3: Write failing SupportingVisual tests**

```python
def test_supporting_visual_pointing_to_text_is_invalid():
    result = validate_paper(*visual_points_to_text(golden_inputs()))
    assert "supporting_visual_requires_figure_or_table" in issue_codes(result)

def test_supporting_visual_requires_existing_insight_and_structured_explanation():
    assert_invalid(visual_with_unknown_insight(golden_inputs()), "unknown_insight")
    assert_invalid(visual_with_empty_or_wrong_explanation(golden_inputs()), "invalid_support_explanation")
```

Run the two tests to RED, then validate candidate kind, insight IDs, support claim kind/content/evidence, exact materialized provenance, and three-visual cap.

- [ ] **Step 4: Write failing Parameter/Ablation tests**

```python
@pytest.mark.parametrize("builder,code", [
    (ablation_with_text_only_evidence, "ablation_requires_visual"),
    (ablation_with_unknown_parameter, "ablation_unknown_parameter"),
    (parameter_with_unknown_ablation, "parameter_unknown_ablation"),
    (parameter_with_unknown_evidence, "parameter_unknown_evidence"),
    (with_duplicate_ablation_id, "duplicate_ablation_id"),
    (with_duplicate_parameter_name, "duplicate_parameter_name"),
])
def test_parameter_ablation_graph_rejects_dangling_or_ambiguous_links(builder, code):
    assert_invalid(builder(golden_inputs()), code)
```

Run to RED, then implement exact name/ID indexes and Figure/Table checks. Do not normalize or infer a new parameter name.

- [ ] **Step 5: Write and pass partial/optional-fact classification tests**

```python
def test_stage3_partial_never_upgrades_to_valid():
    result = validate_paper(*as_stage3_partial(golden_inputs()))
    assert result.status == "partial"
    assert result.validated.report.publication_eligibility == "blocked"

def test_missing_optional_facts_are_valid_and_remain_missing():
    result = validate_paper(*without_optional_facts(golden_inputs()))
    assert result.status == "validated"
    assert result.validated.analysis.parameters == ()
    assert result.validated.analysis.ablations == ()
    assert result.validated.analysis.limitations == ()
    assert result.validated.analysis.links.code_url is None
```

Missing core Insight yields partial; source tampering and dangling references yield invalid; warning-only diagnostics do not block an otherwise valid result.

- [ ] **Step 6: Run GREEN and Stage 3 schema/analyzer regression**

```powershell
uv run pytest tests/analysis/test_validator_rules.py tests/analysis/test_analyzer.py tests/analysis/test_paper_schemas.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add src/zotero_arxiv_daily/analysis/validator.py tests/analysis/test_validator_rules.py
git commit -m "feat: enforce analysis evidence invariants"
```

### Task 4: Add complete-identity validation cache

**Files:**
- Create: `src/zotero_arxiv_daily/analysis/validation_cache.py`
- Create: `tests/analysis/test_validation_cache.py`

**Interfaces:**
- Consumes: CandidatePaper, DocumentGraph, EvidencePacket, PaperAnalysis, `ValidatedPaperAnalysis`.
- Produces: `ValidationCacheIdentity`, `build_validation_cache_identity(...)`, `ValidationCache.read(identity)`, `ValidationCache.write(identity, validated)`.

- [ ] **Step 1: Write failing identity, corrupt-cache, and atomic-write tests**

```python
@pytest.mark.parametrize("field", [
    "validator_version", "candidate_fingerprint", "pdf_sha256", "document_fingerprint",
    "packet_fingerprint", "analysis_schema_version", "analysis_generation_key",
    "analysis_fingerprint",
])
def test_every_required_identity_change_causes_cache_miss(tmp_path, field):
    cache = ValidationCache(tmp_path / "validation")
    original = identity()
    cache.write(original, validated())
    assert cache.read(changed_identity(field)) is None

def test_corrupt_or_oversized_validator_cache_is_a_miss(tmp_path):
    cache = ValidationCache(tmp_path / "validation", max_cache_bytes=128)
    cache.path_for(identity()).parent.mkdir(parents=True)
    cache.path_for(identity()).write_text("not-json", encoding="utf-8")
    assert cache.read(identity()) is None
```

Also assert path containment, envelope paper/cache-key consistency, temp cleanup, and validator-version change miss.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/analysis/test_validation_cache.py -q
```

- [ ] **Step 3: Implement canonical identity and atomic cache**

```python
class ValidationCache:
    def read(self, identity: ValidationCacheIdentity) -> ValidatedPaperAnalysis | None:
        path = self.path_for(identity)
        try:
            if path.stat().st_size > self.max_cache_bytes:
                return None
            envelope = ValidationCacheEnvelope.model_validate_json(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return envelope.validated if envelope.identity == identity else None

    def write(self, identity, validated):
        envelope = ValidationCacheEnvelope(identity=identity, validated=validated)
        payload = envelope.model_dump_json(indent=2) + "\n"
        if len(payload.encode("utf-8")) > self.max_cache_bytes:
            raise ValueError("validation cache payload exceeds configured size")
        target = self.path_for(identity)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            checked = ValidationCacheEnvelope.model_validate_json(
                temporary.read_text(encoding="utf-8")
            )
            if checked != envelope:
                raise ValueError("validation cache revalidation failed")
            os.replace(temporary, target)
            temporary = None
            return target
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
```

Candidate fingerprint includes only paper ID/title/three links. Analysis fingerprint is SHA-256 of canonical deterministic analysis JSON.

- [ ] **Step 4: Run GREEN and prior cache regressions**

```powershell
uv run pytest tests/analysis/test_validation_cache.py tests/analysis/test_analysis_cache.py tests/documents/test_cache.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/analysis/validation_cache.py tests/analysis/test_validation_cache.py
git commit -m "feat: cache deterministic validation reports"
```

### Task 5: Compose batch validation and offline golden CLI

**Files:**
- Create: `src/zotero_arxiv_daily/pipeline/validation.py`
- Modify: `src/zotero_arxiv_daily/pipeline/__init__.py`
- Modify: `config/base.yaml`
- Create: `tests/fixtures/evidence/stage4_golden.json`
- Create: `tests/pipeline/test_validation.py`
- Create: `tests/analysis/test_stage4_offline.py`

**Interfaces:**
- Consumes: `CandidateBatch`, `DocumentBatchResult`, `tuple[EvidencePacket, ...]`, `AnalysisBatchResult`, `ValidationCache`.
- Produces: `ValidationSettings`, `ValidationDependencies`, `build_validation_batch(...) -> ValidationBatchResult`, `run_offline_fixture(...) -> ValidationBatchResult`, and CLI module entry point.

- [ ] **Step 1: Write failing batch isolation/limit tests**

```python
def test_batch_processes_selected_order_at_most_five_and_isolates_failure():
    result = build_validation_batch(candidate_batch(6), documents(), packets(), analyses(), settings(), deps())
    assert [item.paper_id for item in result.results] == selected_ids()[:5]
    assert [item.status for item in result.results[:3]] == ["validated", "invalid", "validated"]

def test_duplicate_or_missing_inputs_are_safe_per_paper_results():
    result = build_validation_batch(candidates(), documents_with_duplicate(), packets(), analyses(), settings(), deps())
    assert result.results[0].status == "failed"
    assert result.results[1].status == "validated"
```

Add run-ID mismatch, missing packet/document/analysis, Stage 3 failed/skipped, cache hit count, and no single-paper exception aborting later papers.

- [ ] **Step 2: Run RED and implement batch composition**

```powershell
uv run pytest tests/pipeline/test_validation.py -q
```

```python
def build_validation_batch(candidates, documents, packets, analyses, settings, dependencies):
    _require_matching_run_ids(candidates, documents, analyses)
    indexes = _build_unique_indexes(documents, packets, analyses)
    results = []
    for paper_id in candidates.selected_for_full_analysis[:5]:
        results.append(_validate_selected_with_cache(paper_id, indexes, settings, dependencies))
    return ValidationBatchResult(
        run_id=candidates.run_id,
        created_at=dependencies.clock(),
        validator_version=settings.validator_version,
        results=tuple(results),
        cache_hit_count=sum(item.cache_hit for item in results),
    )
```

- [ ] **Step 3: Add artificial golden fixture and failing offline test**

The JSON contains only an invented title, one Method text block, one Table with invented caption, one Insight,
one SupportingVisual explanation, one Parameter, and one Ablation. No real paper/PDF/model output or private data.

```python
def test_stage4_offline_golden_is_zero_network_zero_credentials_zero_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = run_offline_fixture(FIXTURE, dry_run=True, cache_root=tmp_path / "cache")
    assert result.results[0].status == "validated"
    assert result.results[0].validated.report.publication_eligibility == "eligible"
    assert result.cache_hit_count == 0
    assert not (tmp_path / "cache").exists()
```

Run to RED before implementing fixture parsing and in-memory validation cache.

- [ ] **Step 4: Implement CLI/config with no credential or network path**

Add exact non-secret config:

```yaml
validation_pipeline:
  schema_version: "1.0"
  validator_version: stage4-v1
  cache_root: cache/validation
  max_papers: 5
  max_cache_bytes: 10485760
```

CLI accepts `--dry-run --offline-fixture PATH --cache-root PATH` and prints only `run_id`, validator version,
valid/partial/invalid/failed/skipped counts, eligible count, and cache hit count. It never reads `os.environ`, constructs a client, or writes cache/output in dry-run.

- [ ] **Step 5: Run GREEN and Stage 3/2/1 focused regressions**

```powershell
uv run pytest tests/pipeline/test_validation.py tests/analysis/test_stage4_offline.py -q
uv run pytest tests/analysis/test_paper_schemas.py tests/analysis/test_prompt.py tests/analysis/test_client.py tests/analysis/test_analysis_cache.py tests/analysis/test_analyzer.py tests/analysis/test_stage3_offline.py tests/documents/test_evidence.py tests/pipeline/test_analysis.py -q
uv run pytest tests/analysis/test_document_schemas.py tests/documents tests/pipeline/test_documents.py -q
uv run pytest tests/analysis/test_stage1_schemas.py tests/interest tests/candidates tests/retriever/test_arxiv_metadata.py tests/pipeline/test_candidates.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add src/zotero_arxiv_daily/pipeline config/base.yaml tests/pipeline/test_validation.py tests/analysis/test_stage4_offline.py tests/fixtures/evidence/stage4_golden.json
git commit -m "feat: build offline stage 4 validation pipeline"
```

### Task 6: Verify, review, fix findings, document, and finish Stage 4

**Files:**
- Modify after final review: `docs/IMPLEMENTATION_PLAN.md`
- Modify after final review: `docs/BASELINE.md`

**Interfaces:**
- Consumes all Stage 4 commits and fresh command evidence.
- Produces a clean, rollback-ready Stage 4 branch with final independent `Ready` verdict.

- [ ] **Step 1: Invoke `superpowers:verification-before-completion` and run fresh layered verification**

```powershell
uv sync --frozen
uv run pytest tests/analysis/test_validation_schemas.py tests/analysis/test_validator_identity.py tests/analysis/test_validator_rules.py tests/analysis/test_validation_cache.py tests/analysis/test_stage4_offline.py tests/pipeline/test_validation.py -q
uv run pytest tests/analysis/test_paper_schemas.py tests/analysis/test_prompt.py tests/analysis/test_client.py tests/analysis/test_analysis_cache.py tests/analysis/test_analyzer.py tests/analysis/test_stage3_offline.py tests/documents/test_evidence.py tests/pipeline/test_analysis.py -q
uv run pytest tests/analysis/test_document_schemas.py tests/documents tests/pipeline/test_documents.py -q
uv run pytest tests/analysis/test_stage1_schemas.py tests/interest tests/candidates tests/retriever/test_arxiv_metadata.py tests/pipeline/test_candidates.py -q
uv run pytest -q
uv run pytest -m "slow or not slow" -q
uv run python -m compileall -q src
git diff --check 8603098474db25826de88af407bdfffb00cdb2b7..HEAD
uv run python -m zotero_arxiv_daily.pipeline.validation --dry-run --offline-fixture tests/fixtures/evidence/stage4_golden.json
```

Record exact counts, durations, exit codes, and the unchanged two Windows spawn failures plus Hugging Face slow failure. Do not call the real application.

- [ ] **Step 2: Run safety and Git tracking scans**

Use `git ls-files` plus PowerShell `Select-String` over tracked/pending text. Report only rule counts and file names, never matching content. Check secret-shaped tokens/private keys, tracked PDF/ZIP, Zotero private paths/terms, candidate/document/analysis/validation caches, viewer reading state, and files over 5 MiB. Confirm `.env`, cache paths, PDFs, private data, and viewer state are ignored while `.env.example` is trackable.

- [ ] **Step 3: Invoke `superpowers:requesting-code-review` for `8603098..HEAD`**

Review Stage 4 boundary, reference completeness, Abstract-only protection, Figure/Table provenance, parameter/ablation mapping, status/eligibility, cache identity, safe messages, dry-run zero-network/cost, and actual tamper coverage.

- [ ] **Step 4: Invoke `superpowers:receiving-code-review` for findings**

For every confirmed Critical/Important: write a focused failing regression test, run RED, apply one root-cause fix, run GREEN and relevant regressions, then commit. Request a second independent review. Repeat until verdict is exactly Ready with no unresolved Critical/Important.

- [ ] **Step 5: Update roadmap and baseline only from fresh evidence**

Mark Stage 4 completed and Stage 5 not started. Record exact commands/counts/failures, validator/schema versions, publication policy, offline/no-cost evidence, safety scan, known limitations, independent review verdict, commit sequence, no origin/upstream push, and rollback instructions.

- [ ] **Step 6: Commit documentation and rerun completion checks**

```powershell
git add docs/IMPLEMENTATION_PLAN.md docs/BASELINE.md
git commit -m "docs: record stage 4 verification"
git status --short --branch
git diff --check 8603098474db25826de88af407bdfffb00cdb2b7..HEAD
git log --oneline --decorate 8603098474db25826de88af407bdfffb00cdb2b7..HEAD
```

- [ ] **Step 7: Invoke `superpowers:finishing-a-development-branch`**

Preserve `feat/stage-4-evidence-validation` and `.worktrees/stage-4-evidence-validation`; do not create a PR, do not push upstream, and do not fabricate `origin`. Final report must include a complete paste-ready Stage 5 session prompt.
