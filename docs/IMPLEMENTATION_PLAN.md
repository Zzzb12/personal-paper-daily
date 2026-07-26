# Personal Paper Daily Implementation Roadmap

## Roadmap rules

- Execute exactly one stage per approved task.
- Begin each stage with Superpowers brainstorming and a stage-specific executable plan.
- Write a failing test before every behavior change.
- Preserve existing core interfaces unless the user explicitly approves a change.
- End every stage with configured tests, stage validators, secret/private-data checks, independent review, and a rollback-ready commit sequence.
- No stage may weaken the evidence rules defined in `docs/PRODUCT_SPEC.md` and `AGENTS.md`.

## Stage 0: Repository initialization and baseline

### Goal

Create the private-development foundation: traceable upstream, isolated branch, factual upstream/Hermes audit, reproducible test baseline, governance, product specification, architecture, long-term roadmap, empty environment template, and safe ignore rules.

### Non-goals

- No Zotero API execution with real credentials.
- No candidate-generation business code.
- No paper/PDF download.
- No LLM, Feishu, viewer, feedback, or new scheduled-pipeline implementation.
- No fix to upstream business code solely to make baseline tests pass.

### Files

- Create `AGENTS.md`.
- Create `.env.example`.
- Modify `.gitignore` without removing upstream rules.
- Create `docs/PRODUCT_SPEC.md`.
- Create `docs/ARCHITECTURE.md`.
- Create `docs/IMPLEMENTATION_PLAN.md`.
- Create `docs/BASELINE.md`.
- Create `docs/superpowers/specs/2026-07-20-stage-0-bootstrap-design.md`.
- Create `docs/superpowers/plans/2026-07-20-stage-0-bootstrap.md`.

### Data structures

No runtime schema is implemented. Documentation defines future `InterestPaper`, `CandidatePaper`, `CandidateBatch`, `PdfArtifact`, `DocumentPage`, `SectionNode`, `VisualArtifact`, `EvidenceRecord`, `ClaimRecord`, `ParameterRecord`, `PaperAnalysis`, `FeedbackRecord`, and `DailyDigest` contracts.

### Test-first implementation steps

1. Verify repository, branch, worktree, and upstream remote before changing files.
2. Run failing `git check-ignore` assertions for representative secrets, PDFs, private Zotero data, caches, local feedback, IDE files, logs, and temporary files.
3. Extend `.gitignore`, create `.env.example`, and rerun ignore assertions.
4. Install the locked Python 3.13/uv environment without changing global configuration.
5. Collect and run default and full upstream pytest commands; use systematic debugging for failures.
6. Inspect upstream and licensed Hermes sources and classify reuse/adaptation/new work.
7. Write documents and run deterministic section/term checks.
8. Run final diff, secret, large-file, private-data, review, and Git checks.

### Acceptance criteria

- `upstream` points to `TideDra/zotero-arxiv-daily`; the development branch is independent.
- Private `origin` exists and is verified private, or authentication absence is explicitly documented.
- Dependency installation and all configured tests have exact recorded results.
- Required documents, `.env.example`, and `.gitignore` checks pass.
- No secret, PDF, Zotero private data, runtime cache, local feedback, or Stage 1 code is tracked.
- `verification-before-completion` and `requesting-code-review` have been completed.
- Final Stage 0 commit/push outcome is accurately reported.

### Risks

- Windows `multiprocessing` differs from Linux CI and can fail upstream timeout tests.
- The slow embedding test depends on Hugging Face connectivity and model availability.
- GitHub CLI authentication may be absent.
- Documentation can drift from actual source unless every claim is tied to inspection or command output.

### Rollback

Keep the upstream clone and approved design commits. Revert only the final Stage 0 bootstrap commit to remove added governance/docs/config artifacts. Delete no private external reference material. If no commit exists, unstage the exact Stage 0 files and preserve them for correction.

### Suggested commits

- `docs: record stage 0 bootstrap design`
- `docs: allow licensed Hermes source reuse`
- `chore: bootstrap personal paper daily project`

## Stage 1: Zotero interest reading and candidate paper JSON

**Status:** Completed on 2026-07-20; verification results and known baseline failures are recorded in the Stage 1 completion notes and Git history.

### Goal

Read the approved Zotero collection tree, retrieve metadata-only arXiv candidates, rank them against the interest corpus, and persist a validated candidate JSON batch without downloading full PDFs or performing full LLM analysis.

### Non-goals

- No full PDF download or parsing.
- No LLM paper analysis, evidence extraction, viewer, Feishu, or feedback UI.
- No bioRxiv/medRxiv expansion.
- No replacement of the existing `Executor` entry point.

### Files

- Create `src/zotero_arxiv_daily/analysis/schemas.py` for Stage 1 records and versioned envelopes.
- Create `src/zotero_arxiv_daily/interest/base.py` and `interest/zotero.py`.
- Create `src/zotero_arxiv_daily/candidates/store.py`.
- Create `src/zotero_arxiv_daily/pipeline/candidates.py`.
- Modify `src/zotero_arxiv_daily/retriever/arxiv_retriever.py` through a backward-compatible metadata-only adapter.
- Modify `src/zotero_arxiv_daily/reranker/base.py` only through tested structured adapters/caching hooks.
- Modify `config/base.yaml` and `config/custom.yaml` for approved collection paths, categories, and limits.
- Create matching tests under `tests/interest/`, `tests/candidates/`, `tests/pipeline/test_candidates.py`, and schema tests.

### Data structures

- `InterestPaper`: stable ID, title, abstract, collection paths, Zotero added date, feedback weight.
- `CandidatePaper`: arXiv identity/version, metadata, URLs, and normalized categories without full text.
- `RankingRecord`: embedding score, optional LLM score field left `null`, final score, rank, reason, model/config versions.
- `CandidateBatch`: run metadata, up to 30 candidates, up to 15 LLM-rerank IDs, up to five future full-analysis IDs, and `schema_version`.

### Test-first implementation steps

1. Write schema tests for valid round trips, invalid IDs/dates/categories, unknown fields, and deterministic JSON.
2. Write Zotero provider tests for recursive include paths, exclusion precedence, pagination, empty collections, zero-collection papers, missing abstracts, and parent cycles.
3. Implement an injected Zotero gateway adapter around `pyzotero` and reuse existing glob behavior.
4. Write metadata-only arXiv tests proving no tar/HTML/PDF extraction function is called.
5. Implement retrieval/deduplication for `cs.CV`, `cs.LG`, and `cs.AI` with bounded retries/timeouts.
6. Write ranking tests for stable ordering, 30/15/5 limits, empty corpus, duplicate versions, and cached embeddings.
7. Implement the candidate store with atomic validated writes under ignored run data.
8. Add a dry-run CLI and integration test using fake Zotero/arXiv/embedding clients.

### Acceptance criteria

- Include paths are `PaperDaily/00-Seeds/**`, `PaperDaily/03-Read/**`, and `PaperDaily/04-Favorite/**`; `PaperDaily/99-Exclude/**` always wins.
- Only `cs.CV`, `cs.LG`, and `cs.AI` are fetched by default.
- Candidate JSON validates and contains at most 30 ranked papers, at most 15 LLM-rerank slots, and at most five future full-analysis selections.
- No PDF/full-text extraction or paid API is invoked in the acceptance run.
- Private Zotero data and candidate runtime files remain ignored.
- Existing upstream tests retain their baseline behavior.

### Risks

- Zotero collection paths can be cyclic or missing.
- Embedding model download can be slow or unavailable.
- Eager full-text work in the current arXiv conversion path can violate the cost boundary.
- Ranking changes may alter existing email order.

### Rollback

Disable the new candidate CLI/config and retain existing `Executor` behavior. Remove only new adapters/stores and restore touched retriever/reranker files from the stage commits; persisted candidate batches are disposable ignored artifacts.

### Suggested commits

- `test: define interest and candidate schemas`
- `feat: add Zotero interest provider`
- `feat: retrieve metadata-only arXiv candidates`
- `feat: rank and persist candidate batches`
- `docs: record stage 1 operation and limits`

## Stage 2: PDF text, page, and Figure/Table extraction

**Status:** Completed on 2026-07-20; verification results, parser limits, and known baseline failures are recorded in `docs/BASELINE.md` and the Stage 2 design/plan documents.

**Implementation note:** The approved implementation uses Docling Standard as the layout-aware parser with PyMuPDF for deterministic PDF inspection and evidence-image rendering. This supersedes the roadmap's original PyMuPDF-first adapter wording without changing the Stage 2 product boundary. Runtime-generated PDF fixtures remain untracked.

### Goal

Download only selected PDFs and produce a page-aware document graph containing text blocks, sections, figures, tables, captions, coordinates, and stable mappings.

### Non-goals

- No Chinese Insight/Method generation.
- No LLM evidence interpretation.
- No Feishu/viewer delivery.
- No OCR guarantee for scanned PDFs in the first implementation.

### Files

- Create `src/zotero_arxiv_daily/documents/downloader.py`.
- Create `src/zotero_arxiv_daily/documents/parser.py`.
- Create `src/zotero_arxiv_daily/documents/sections.py`.
- Create `src/zotero_arxiv_daily/documents/captions.py`.
- Create `src/zotero_arxiv_daily/documents/mapper.py`.
- Extend `src/zotero_arxiv_daily/analysis/schemas.py` with document records.
- Create `tests/documents/` and `tests/fixtures/pdf_factory.py` to generate small deterministic PDFs inside pytest temporary directories at runtime; do not track PDF binaries.

### Data structures

- `PdfArtifact`: URL, hash, byte size, content type, local path, timestamp.
- `DocumentBlock`: block ID, page anchors, bbox, text, block type.
- `DocumentPage`: parser index, original PDF page, display label, dimensions, blocks.
- `SectionNode`: hierarchy, normalized section kind, block/page range.
- `VisualArtifact`: Figure/Table kind, label, caption, page, section, bbox, image path.
- `DocumentGraph`: resolvable maps among pages, blocks, sections, and visuals.

### Test-first implementation steps

1. Write downloader tests for cache hits, streamed success, wrong media/signature, over-size, truncation, timeout, retry, and partial cleanup.
2. Implement content-addressed downloads with explicit connect/read timeouts.
3. Write parser tests against runtime-generated one-column, two-column, rotated, appendix, table, and malformed PDF fixtures.
4. Adapt PyMuPDF/pymupdf4llm behind an injected parser and preserve page/bbox metadata.
5. Write section hierarchy tests for all preferred section kinds and unknown headings.
6. Write caption tests for Figure/Table labels, subfigures, multi-line captions, duplicate labels, and missing captions.
7. Implement mapping invariants and property tests for every referenced ID/page/bbox.
8. Add an offline integration test from cached candidate selection to validated document graph.

### Acceptance criteria

- Only Stage 1 full-analysis selections are downloaded, maximum five per batch.
- Every extracted block and visual resolves to an original PDF page.
- Every Figure/Table retains label, caption or explicit `null`, section, bbox, and image path.
- Parser/document caches are versioned and invalidated by PDF hash/parser settings.
- A malformed paper fails independently without corrupting other outputs.
- No generated PDF or parse cache is tracked by Git.

### Risks

- PDF layout libraries can change block ordering between versions.
- Display page labels may differ from physical page indices.
- Multi-page tables/captions and vector graphics are difficult to associate.
- Parser subprocess timeouts behave differently on Windows and Linux.

### Rollback

Disable the document pipeline and delete ignored parser caches. Candidate batches remain valid. Revert the Stage 2 commits without changing Stage 1 schemas except through a backward-compatible schema-version rollback/migration.

### Suggested commits

- `test: define page-aware document contracts`
- `feat: add validated PDF downloader`
- `feat: parse PDF pages and sections`
- `feat: extract and map figures and tables`
- `docs: document Stage 2 parser limits`

## Stage 3: Structured Chinese Insight, Method, and Ablation analysis

**Status:** Completed on 2026-07-20; offline verification, independent review, cost boundaries, and the three unchanged environment-dependent regression failures are recorded in `docs/BASELINE.md` and the Stage 3 design/plan documents.

### Goal

Generate a strict Chinese `PaperAnalysis` from the mapped full document, following the fixed Insight/evidence/Method/parameters/ablations/results/limitations order and preserving claim-to-evidence candidates.

### Non-goals

- No final evidence enforcement; hard validation belongs to Stage 4.
- No viewer or Feishu delivery.
- No analysis of more than five papers per run.
- No model-specific prompt optimization beyond the first approved provider contract.

### Files

- Extend `src/zotero_arxiv_daily/analysis/schemas.py` with claim/parameter/analysis records.
- Create `src/zotero_arxiv_daily/documents/evidence.py` for deterministic evidence retrieval.
- Create `src/zotero_arxiv_daily/analysis/analyzer.py`.
- Create versioned prompt templates under `src/zotero_arxiv_daily/analysis/prompts/`.
- Add configuration for `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, request limits, and cache path without secrets.
- Create `tests/analysis/test_schemas.py`, `test_evidence.py`, `test_analyzer.py`, and prompt fixtures.

### Data structures

- `EvidenceRecord` candidate with page/section/visual bindings and source classification.
- `ClaimRecord` for Insights, Method, results, and limitations.
- `MethodModule` with purpose and evidence IDs.
- `ParameterRecord` with name, symbol, role, value, selection method, tuning scope, evidence, and ablation claim IDs.
- `PaperAnalysis` containing all required Chinese fields, links, evidence candidates, and generation metadata.

### Test-first implementation steps

1. Write schema tests for all required/optional fields and `inferred` invariants.
2. Write deterministic evidence retrieval tests that prioritize approved sections and reject Abstract-only Insight support.
3. Define a versioned structured-output prompt that requests no unsupported data and allows `null`.
4. Write fake-client tests for valid output, malformed JSON, missing fields, unsupported labels/values, rate limits, timeout, and truncation.
5. Implement analyzer retries, schema parsing, per-paper isolation, and content/model/prompt/schema cache keys.
6. Write narrative-order and Chinese-field completeness tests.
7. Add a zero-cost integration test using canned mapped documents and fake LLM responses.

### Acceptance criteria

- At most five selected papers are analyzed.
- Every required field is present or explicitly `null`; user rendering can show “论文未明确提供”.
- Every Insight and ablation includes evidence candidate IDs.
- Parameters record symbol, role, value, selection method, per-model tuning, and ablation linkage.
- Author statements, system summaries, and system inferences are distinct; inference uses `inferred=true`.
- Repeated identical analysis uses cache and performs no duplicate expensive call.

### Risks

- Structured output can be syntactically valid but evidence-inconsistent.
- Long documents can exceed model context.
- Provider behavior/model versions can change.
- Cached prompts may contain private Zotero-derived context if scope is not minimized.

### Rollback

Disable analysis invocation and retain mapped documents. Delete ignored LLM caches for the affected prompt/schema version. Revert prompt/analyzer commits while retaining compatible schema migrations.

### Suggested commits

- `test: define structured Chinese analysis schema`
- `feat: retrieve evidence candidates from mapped documents`
- `feat: generate cached structured paper analyses`
- `docs: document analysis prompt and cost boundary`

## Stage 4: Evidence validation and hallucination prevention

**Status: completed on 2026-07-20. Stage 5 is implemented on the isolated `feat/stage-5-static-viewer` branch; final verification and review evidence are recorded in `docs/BASELINE.md`.**

The approved implementation is documented in
`docs/superpowers/specs/2026-07-20-stage-4-evidence-validation-design.md` and
`docs/superpowers/plans/2026-07-20-stage-4-evidence-validation.md`. The delivered
boundary is a deterministic validator, strict validation/report schemas, a
complete-identity validation cache, batch failure isolation, and an artificial
offline golden fixture. It does not mutate or remove Stage 3 claims: any serious
claim or provenance error makes the paper `invalid` and publication-blocked;
Stage 3 `partial` remains `partial` and publication-blocked.

### Goal

Reject or downgrade every analysis claim that cannot be resolved to real document evidence, with strict Figure/Table, parameter, ablation, source-type, and inference checks.

### Non-goals

- No new analysis content generation; corrective retry was not implemented.
- No UI or delivery implementation.
- No confidence-based bypass of missing references.

### Files

- Create `src/zotero_arxiv_daily/analysis/validator.py`,
  `validation_schemas.py`, and `validation_cache.py`.
- Add `src/zotero_arxiv_daily/pipeline/validation.py` as the only Stage 4
  publication-eligibility boundary.
- Add focused schema, identity/provenance, rule, cache, pipeline, and offline tests
  under `tests/analysis/` and `tests/pipeline/`.
- Add the artificial golden fixture under `tests/fixtures/evidence/`.

### Data structures

- `ValidationIssue`: code, severity, field/claim path, paper/evidence/visual IDs, safe message.
- `ValidationReport`: valid/partial/invalid status, accepted/rejected claim IDs, issue list, validator version.
- Validated `PaperAnalysis` with no dangling evidence IDs and explicit partial-analysis markers.

### Test-first implementation steps

1. Write failing tests for nonexistent page/section/visual/evidence references.
2. Add tests rejecting Abstract-only Insight, invented Figure/Table labels, caption mismatches, unsupported numerical values, and unbound ablations.
3. Add tests for missing limitations/parameters that must remain `null` rather than inferred.
4. Add cross-field tests for source type and `inferred` flag consistency.
5. Implement pure deterministic validation against the document graph/evidence registry.
6. Add cache-integrity tests binding Stage 3 status, analysis content, validator version, and validation report content.
7. Add negative tests ensuring every eligible claim traverses to real evidence and every ablation conclusion cites its declared visual.

### Acceptance criteria

- No published Insight is Abstract-only.
- No published Figure/Table/parameter/result references an unresolved artifact or text span.
- Every published ablation binds to its Figure/Table.
- Missing facts render as `null`/“论文未明确提供”.
- Invalid claims are never silently removed or rewritten; the immutable paper analysis is blocked as `invalid`.
- Validation is deterministic, offline, and fully tested.

### Risks

- Text normalization may create false mismatches.
- Parser mistakes can look like analysis hallucination.
- Overly strict rules can suppress valid but indirectly stated conclusions.
- Corrective retries can create loops or increased cost.

### Rollback

Disable publication of newly generated analyses rather than bypassing validation. Revert validator changes only together with the analysis schema version; retain raw mapped documents and draft analyses for revalidation.

### Suggested commits

- `test: encode evidence integrity invariants`
- `feat: validate claims and visual bindings`
- `feat: quarantine unsupported analysis content`
- `docs: document hallucination prevention policy`

## Stage 5: Static web reader

**Status: implemented on 2026-07-21.**

### Goal

Build an accessible, responsive, offline static reader from Stage 4 validation results. Only a `valid` and eligible paper is published in full; a blocked partial paper is published only when explicitly allowed, and invalid/failed/skipped papers never obtain a public page.

### Non-goals

- No Feishu sending.
- No authenticated multi-user server.
- No public publication of private feedback/Zotero data.
- No Stage 8 feedback-to-ranking integration.

### Files

- Create `src/zotero_arxiv_daily/viewer/builder.py`.
- Create schema-driven `viewer/` builder, renderer, publication policy, filesystem, asset publisher, CSS and local favicon.
- Add a fixture-only offline CLI and focused `tests/viewer/` coverage.
- Add a safe local evidence-image copy path and structured missing-image fallback.

### Data structures

- `ViewerSettings`, `PublicationDecision`, `IndexPageModel`, `PaperPageModel`, and `BuildManifest` are frozen, strict Pydantic contracts.
- `EvidenceImagePublisher` copies only configured local evidence roots into content-addressed `assets/evidence/` paths.

### Test-first implementation steps

1. Write RED tests for publication policy, escaping, path containment, stale-page deletion, and image asset handling.
2. Render semantic index/detail HTML in the required reading order with CSP and local-only assets.
3. Publish controlled evidence images with SHA-256 names; show a text fallback when no approved image exists.
4. Verify offline CLI, a local-server Playwright smoke test, desktop/mobile layout, and full regression boundaries.

### Acceptance criteria

- Desktop and mobile layouts remain readable without horizontal overflow.
- Invalid, failed, skipped, and blocked papers never retain a stale public detail page.
- Full analyses use the required narrative order and show evidence provenance/support explanations.
- Missing or partial analysis is explicit.
- Generated artifacts contain no secret/private Zotero data, local feedback, third-party code, CDN, runtime API, or browser storage.

### Risks

- Large evidence images/site JSON can make mobile loading slow.
- The inspected Hermes source has no license file; no Hermes code, data, images, cache, or history is reused.
- Static-site URLs can break when deployed under a repository subpath.
- Snapshot tests can be brittle across browsers/fonts.

### Rollback

Stop invoking the viewer CLI and remove the ignored configured output directory. Revert viewer-specific commits; Stage 4 validation artifacts remain untouched.

### Suggested commits

- `test: define static reader data contract`
- `feat: build validated static site data`
- `feat: adapt responsive paper reader`
- `test: verify accessibility and mobile layouts`

## Stage 6: Feishu delivery

**Status: implemented on 2026-07-22.**

The delivered boundary is documented in
`docs/superpowers/specs/2026-07-21-stage-6-feishu-delivery-design.md` and
`docs/superpowers/plans/2026-07-21-stage-6-feishu-delivery.md`. It provides a
validated digest renderer, an injectable bounded-retry client, an in-process
idempotency ledger, and a preview-first fixture CLI. Real delivery remains
opt-in through the exact `--send` flag and was not exercised during acceptance.

### Goal

Render and send a concise, attractive daily Feishu digest containing at most five detailed papers and a link to the complete static reader.

### Non-goals

- No GitHub Actions schedule; Stage 7 owns automation.
- No email removal.
- No more than five detailed paper items.
- No delivery of unvalidated claims.

### Files

- Create `src/zotero_arxiv_daily/delivery/feishu.py` with separate renderer/client interfaces.
- Add Feishu config mapping for `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and `FEISHU_CHAT_ID`.
- Adapt licensed Hermes Chinese digest layout/field ordering.
- Create `tests/delivery/test_feishu_renderer.py` and `test_feishu_client.py`.
- Add a no-send local preview command.

### Data structures

- `FeishuPayload`: validated card/Markdown structure and size metadata.
- `DeliveryRequest`: digest ID, chat ID reference, payload, idempotency key.
- `DeliveryReceipt`: remote message ID, status, attempts, timestamps, redacted error category.

### Test-first implementation steps

1. Write pure renderer tests for zero/one/five papers, partial analysis, missing code link, and payload size limits.
2. Implement compact Chinese rendering from validated `DailyDigest` only.
3. Write fake-client tests for token acquisition, send success, 401 refresh, 429/backoff, 5xx retry, timeout, and permanent 4xx failure.
4. Implement environment-only credentials, redacted logging, explicit timeouts, and bounded retries.
5. Write idempotency-ledger tests preventing duplicate daily sends.
6. Run preview with fixture data; perform a real send only after the user configures local environment variables and explicitly authorizes it.

### Acceptance criteria

- Detailed Feishu output contains no more than five papers.
- Every rendered claim comes from a validated analysis.
- Payload remains concise/mobile-readable and links to the full site.
- No credential value is logged or persisted.
- Transient failures retry within bounds; duplicate digest IDs do not resend.
- Unit/integration tests use fakes and incur no API cost.

### Risks

- Feishu card/API size and rate limits can change.
- Token expiry during retry can create duplicates without idempotency.
- A real chat ID misconfiguration can send to the wrong audience.
- Markdown/card rendering differences can reduce readability.

### Rollback

Disable the Feishu delivery stage while retaining rendered previews and the static site. Revoke/rotate credentials outside Git if a destination mistake occurs. Revert client/renderer commits independently because their interface is separated.

### Suggested commits

- `test: define Feishu digest contract`
- `feat: render concise Chinese Feishu digest`
- `feat: send Feishu messages safely`
- `docs: document preview and credential setup`

## Stage 7: GitHub Actions daily automation

**Status: completed locally on 2026-07-22.** Final fixture verification, independent
review, known environment-dependent failures, external steps not executed, and
rollback evidence are recorded in `docs/BASELINE.md`.

The delivered boundary is documented in
`docs/superpowers/specs/2026-07-22-stage-7-github-actions-automation-design.md`
and `docs/superpowers/plans/2026-07-22-stage-7-github-actions-automation.md`.
It provides a versioned injectable daily CLI, strict atomic run manifest,
complete-identity safe cache envelope, audited viewer artifact, persistent Feishu
idempotency ledger, pinned least-privilege workflow, and explicit live/send/Pages
gates. Default scheduled/manual/local acceptance remains fixture-only dry-run and
no-send.

### Goal

Run the validated pipeline daily and on manual dispatch using least-privilege GitHub Actions, safe secrets, versioned caches, static Pages deployment, and partial-failure reporting.

### Non-goals

- No new recommendation, parsing, analysis, viewer, or feedback behavior.
- No printing secret-bearing generated config.
- No automatic write to upstream.
- No public repository conversion.

### Files

- Create/replace a personal daily workflow under `.github/workflows/personal-paper-daily.yml`.
- Adapt `.github/workflows/ci.yml` for new tests without removing upstream coverage.
- Adapt licensed Hermes `.github/workflows/pages.yml` for static-site artifacts.
- Create `src/zotero_arxiv_daily/pipeline/daily.py` CLI orchestration if not completed earlier.
- Create workflow/static validation tests and documented repository variables/secrets.

### Data structures

- `RunManifest`: run ID, trigger, config hash, stage statuses, counts, costs, partial failures, artifact hashes.
- Cache-key records for embeddings, PDFs/parses, and LLM outputs.
- `DailyDigest` reused for delivery/publication.

### Test-first implementation steps

1. Write CLI orchestration tests with fake providers for success, empty, partial, and failed runs.
2. Write cache-key and restore-safety tests, ensuring secrets/private raw Zotero data never enter public artifacts.
3. Implement a single compositional daily CLI with stage status output.
4. Write workflow checks for pinned actions, least permissions, concurrency, schedule/manual dispatch, timeouts, and no secret echo.
5. Adapt Pages deployment to upload only the static viewer artifact.
6. Run local workflow validation and a fake/no-send dry run.
7. Configure GitHub Secrets/variables outside code and manually dispatch one controlled run.

### Acceptance criteria

- Scheduled and manual triggers run the same versioned CLI.
- Workflow permissions are least-privilege and no step prints custom config containing secrets.
- Cache keys include all required model/parser/prompt/schema versions.
- One-paper failure yields a partial run instead of discarding successful papers.
- Static deployment and Feishu delivery have separate recorded outcomes.
- No write targets upstream; origin remains private unless the user deliberately publishes only the static artifact.

### Risks

- Private-repository Actions minutes and cache quotas are limited.
- Large model/PDF caches may exceed quotas.
- Schedule timing and arXiv availability vary on weekends/holidays.
- Automatic Pages publication can expose unintended data without artifact validation.

### Rollback

Disable the schedule or workflow file, retain manual execution, and redeploy the last known-good static artifact. Revoke affected secrets in GitHub settings rather than editing history. Revert workflow commits without rolling back validated domain modules.

### Suggested commits

- `test: define daily pipeline run manifest`
- `feat: orchestrate the daily pipeline`
- `ci: add safe daily personal paper workflow`
- `ci: publish validated static reader`
- `docs: document Actions secrets and recovery`

## Stage 8: Read, favorite, and irrelevant feedback

**Status: completed locally on 2026-07-26.** Final independent whole-branch
re-review reported Spec Compliance `PASS`, Code Quality `PASS`, and no remaining
Critical/Important/Minor findings. No real user feedback, credentials,
Zotero/private-library data, browser snapshot, or external operation is used in the
implementation record.

### Goal

Persist private read/favorite/irrelevant states, expose them in the reader, and feed explicit preference signals back into future ranking without publishing local state.

### Non-goals

- No collaborative accounts or cloud database.
- No implicit behavioral tracking.
- No retroactive rewriting of evidence or analysis.
- No unreviewed automatic deletion from Zotero.
- No Pages write-back or scheduled/private-state synchronization.

### Configuration and privacy boundary

- Browser actions update immediate same-origin `localStorage`. Reaching the
  authoritative local store requires an explicit feedback export followed by the
  local import CLI; GitHub Pages cannot write back to that store.
- The CLI default private root is `data/private-feedback/`, which is Git-ignored and
  constrained as a local boundary. Production configuration may set only the optional
  `candidate_pipeline.feedback.store_path` in `config/base.yaml`; the tracked default
  is `null` and `favorite_delta` defaults to `0.05` (bounded to `0.10`). Only
  `favorite_delta` enters configuration hashing; every other feedback field is
  excluded from that public-safe hash input.
- `irrelevant` exact-ID veto happens before embedding or paid work; `favorite` adds a
  capped delta and `read` has no ranking effect. Stage 4 remains the only publication
  eligibility gate.
- Workflow cache/upload paths are explicit allowlists and exclude feedback state,
  bundles, browser snapshots, private stores, migration backups, `.env`, and Zotero
  data. Scheduled/manual defaults remain fixture dry-run and no-send.

### Files

- Create `src/zotero_arxiv_daily/viewer/feedback.py`.
- Extend viewer UI/state code using licensed Hermes favorite implementation.
- Extend Stage 1 interest/ranking adapters to consume a privacy-safe feedback projection.
- Create `tests/viewer/test_feedback.py`, UI interaction tests, and ranking-feedback tests.
- Add schema migrations for `FeedbackRecord`.

### Data structures

- `FeedbackRecord`: paper ID, `read`, `favorite`, `irrelevant`, timestamp, source, schema version.
- `FeedbackCommand`: paper ID, action, desired value, idempotency key.
- `InterestFeedbackProjection`: positive/negative ranking weight without full local UI history.

### Test-first implementation steps

1. Write state tests for idempotent toggles, invalid IDs, and corrupted/migrated stores.
2. Encode conflict policy: irrelevant removes favorite; later favorite removes irrelevant; read coexists.
3. Implement atomic private local storage and optional browser `localStorage` adapter.
4. Write UI tests for controls, keyboard access, filters, and persistence.
5. Write ranking tests proving favorite increases and irrelevant decreases influence without overriding deterministic exclusions.
6. Add import/export backup tests that redact no content unexpectedly and never auto-commit.

### Acceptance criteria

- Read, favorite, and irrelevant persist across local reader sessions.
- State transitions obey the documented conflict policy and are idempotent.
- Feedback files are ignored and absent from public site artifacts.
- Ranking consumes explicit signals with testable bounded weights.
- Existing licensed Hermes favorite behavior remains available through the adapted interface.
- No Zotero mutation occurs.

### Risks

- Browser/local server stores can diverge.
- Paper version/ID normalization can orphan feedback.
- Strong negative weighting can create filter bubbles.
- Static hosting limits cross-device synchronization.

### Rollback

Disable feedback influence while preserving an explicit private-store backup. Revert
the UI/ranking adapters; the static reader remains read-only. Migrate forward/back
using schema-versioned exports rather than deleting user state. Do not use Pages,
Actions cache, or Git history as a feedback backup.

### Suggested commits

- `test: define private feedback state transitions`
- `feat: persist read favorite and irrelevant state`
- `feat: add accessible feedback controls`
- `feat: apply bounded feedback to ranking`

### Completion verification

- Frozen sync checked 172 packages; the final expanded Stage 8 focused group passed
  with `251 passed in 15.87s`, and the explicit Stage 7 plus Stage 6/5/4
  regression group passed with `417 passed in 18.37s`.
- Final default `pytest -q` produced `705 passed`, `2 failed`, `1 deselected` in
  `29.05s`; both failures are the unchanged Windows one-second multiprocessing
  spawn tests. The explicit `slow or not slow` command was actually run without
  filters, then hit the 600-second verification cap with exit 124 and no final
  pytest summary, consistent with the known uncached local-reranker/Hugging Face
  environment block. It is not recorded as a pass or silently skipped.
- Compilation, diff, workflow static validation (11 passed), tracked hygiene,
  fixture daily dry-run, synthetic feedback-import dry-run, and generated artifact
  audit passed. The fixture daily run published one item, delivered zero, and its
  six-file viewer audit matched SHA-256
  `7ca1c753d2ed4828fabe5f29387c97fd3fc55d88c996c2e67ea5aa039cf92a13`.
- No real GitHub dispatch/Pages deploy, Zotero or LLM access, model/paper download,
  Feishu send, push, PR, merge, or upstream mutation occurred. The user-accepted
  Task 2 historical TDD-evidence deviation remains a process-history note; it does
  not reduce current coverage.
- Independent review used exact Stage 7 base
  `c822897f38573d5fa3b09b19513dc169529854de`. All confirmed findings received
  focused RED coverage before repair; final re-review reported no
  Critical/Important/Minor findings and both verdicts `PASS`.

## Stage 9: Quality, cost, and runtime optimization

### Goal

Measure and improve recommendation quality, evidence integrity, API/model cost, cache effectiveness, and daily runtime without changing product semantics silently.

### Non-goals

- No feature expansion unrelated to measured bottlenecks.
- No reduction of evidence validation to improve throughput.
- No unbounded benchmark dataset or paid evaluation run.
- No model/provider switch without comparative evidence and approval.

### Files

- Create `src/zotero_arxiv_daily/observability/metrics.py`.
- Create benchmark/evaluation tools under `tools/benchmarks/` with small licensed fixtures.
- Add quality/cost/performance tests under `tests/benchmarks/` and regression budgets to CI where stable.
- Tune cache/concurrency/config modules identified by profiling.
- Update `docs/BASELINE.md` or a versioned benchmark report with before/after evidence.

### Data structures

- `RunMetrics`: per-stage latency, counts, bytes, retries, cache hit rate, model/token/cost estimates, failure classes.
- `QualityEvaluation`: ranked-paper relevance labels, evidence precision/recall checks, unsupported-claim rate, missing-field rate.
- `PerformanceBudget`: maximum runtime/memory/network/cost thresholds per batch.

### Test-first implementation steps

1. Define metric schemas and tests proving secrets/private text are never emitted.
2. Build a small reproducible offline benchmark from licensed fixtures and user-provided relevance labels.
3. Measure current ranking quality, evidence validation outcomes, runtime, memory, cache hits, and estimated cost.
4. Profile the dominant bottleneck before selecting one optimization.
5. Add a failing performance/regression test for that bottleneck.
6. Implement one bounded optimization, rerun correctness and benchmark suites, and compare confidence intervals or repeated-run distributions.
7. Repeat only for independently justified bottlenecks; document trade-offs and provider/model changes.

### Acceptance criteria

- Every optimization has a before/after measurement and retains all correctness/evidence tests.
- Expensive calls and token/cost estimates are observable without exposing prompts/private text.
- Cache hit/miss reasons are measurable and version-safe.
- Daily runtime and cost meet an explicitly recorded budget for the 30/15/5 defaults.
- No quality regression is hidden behind average latency improvements.
- CI includes stable regression checks that do not require paid APIs.

### Risks

- Microbenchmarks may not represent GitHub Actions or real PDFs.
- Model/provider upgrades can change quality and cost simultaneously.
- Excessive concurrency can violate rate limits or increase memory.
- Caching can preserve stale or invalid analysis if keys are incomplete.

### Rollback

Feature-flag or revert each optimization independently. Invalidate only affected cache versions. Restore previous model/config versions and benchmark reports; retain metrics needed to explain the rollback.

### Suggested commits

- `test: define privacy-safe run metrics`
- `perf: add reproducible pipeline benchmarks`
- `perf: optimize measured pipeline bottleneck`
- `docs: record quality cost and runtime results`
