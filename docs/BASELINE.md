# Stage 0 Baseline

## Snapshot

| Item | Value |
|---|---|
| Repository | `TideDra/zotero-arxiv-daily` cloned locally as `personal-paper-daily` |
| Upstream URL | `https://github.com/TideDra/zotero-arxiv-daily.git` |
| Upstream remote | `upstream` (fetch and push URL recorded; no upstream write performed) |
| Upstream default branch | `main` |
| Upstream commit | `05b20ec5c14ef82f8634c21a0876acd40e02c2b2` |
| Upstream description | `v1.1.0-8-g05b20ec` |
| Development branch | `chore/bootstrap-personal-paper-daily` |
| Local repository path | `F:\Yan_0\Video_generaton\PaperDaily\personal-paper-daily` |
| Origin | Absent because GitHub CLI is not authenticated |
| License | GNU Affero General Public License v3.0 (`AGPL-3.0`) |
| Baseline date | 2026-07-20 (Asia/Taipei) |

The clone was created from the upstream default branch, the original clone remote was renamed to `upstream`, and all Stage 0 work occurs on the independent development branch. No pull request, force push, public repository, or upstream mutation was performed.

## Current directory structure

```text
personal-paper-daily/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml
│   │   ├── keep-alive.yml
│   │   ├── main.yml
│   │   └── test.yml
│   └── copilot-instructions.md
├── assets/
├── config/
│   ├── base.yaml
│   ├── custom.yaml
│   └── default.yaml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── BASELINE.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── PRODUCT_SPEC.md
│   └── superpowers/
├── src/zotero_arxiv_daily/
│   ├── reranker/
│   ├── retriever/
│   ├── construct_email.py
│   ├── executor.py
│   ├── main.py
│   ├── protocol.py
│   └── utils.py
├── tests/
│   ├── reranker/
│   ├── retriever/
│   └── utils/
├── .env.example
├── .gitignore
├── .python-version
├── AGENTS.md
├── CLAUDE.md
├── LICENSE
├── README.md
├── pyproject.toml
└── uv.lock
```

The generated `.venv` and test caches are ignored and omitted from the tree. No Stage 1 package directories were created.

## Toolchain

| Tool | Result |
|---|---|
| Git | 2.36.1.windows.1 |
| GitHub CLI | 2.92.0; installed but `gh auth status` reports no authenticated host |
| System `python` | 3.11.9; incompatible with the declared project requirement |
| Windows `py` launcher | Broken reference to unavailable `C:\Python312\python.exe` |
| System pip | 24.3.1 on Python 3.11 |
| Poetry | Not installed |
| PDM | Not installed |
| Global uv | Not installed |
| Stage 0 tool uv | 0.11.29 in repository-external `.stage0-tools` virtual environment |
| uv-managed project Python | 3.13.14 |
| Project requirement | `.python-version` is `3.13`; `pyproject.toml` declares `requires-python = ">=3.13"` |
| Package/build manager | uv with `uv.lock` and `uv_build` |

Python 3.13 is a hard upstream requirement rather than a cosmetic preference. Stage 0 used uv's managed Python without modifying PATH or global Python configuration. The repository-external tool environment is:

`F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools`

The bundled `rg.exe` could not start in this Windows app environment due to access denial, so repository searches used PowerShell `Get-ChildItem` and `Select-String`.

## Dependency installation

Commands actually run:

```powershell
python -m venv F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\python.exe -m pip install --upgrade pip uv
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe python install 3.13
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe sync --frozen
```

Results:

- uv installed successfully as 0.11.29.
- CPython 3.13.14 installed successfully through uv.
- `uv sync --frozen` installed 122 locked packages and the local project.
- The environment uses CPU PyTorch 2.11.0.
- uv could not hardlink across the relevant filesystems and fell back to copies. This affects installation performance/disk use, not correctness.
- No project dependency or lock file was modified.

Installation accessed PyPI, the PyTorch CPU index, and uv's Python distribution source. It used no project credentials and incurred no API fee.

## Test baseline

### Collection

Command:

```powershell
uv run pytest --collect-only -q
```

Result: 82 of 83 tests selected by the default marker expression; one slow test deselected. Collection completed in 35.44 seconds on the first run.

Slow-test collection:

```powershell
uv run pytest -m slow --collect-only -q
```

Result: one test, `tests/reranker/test_local_reranker.py::test_local_reranker`.

### Default upstream suite

Command:

```powershell
uv run pytest
```

Result on Windows/Python 3.13.14:

- 82 selected.
- 80 passed.
- 2 failed.
- 1 slow test deselected.
- Runtime: 9.97 seconds.

Failing tests:

1. `test_run_with_hard_timeout_returns_value`
2. `test_run_with_hard_timeout_returns_none_on_failure`

Both are in `tests/retriever/test_arxiv_retriever.py`.

A fresh completion-verification rerun after all Stage 0 document/config changes reproduced the same result: 80 passed, 2 failed, and 1 deselected in 5.94 seconds. This confirms that Stage 0 introduced no additional default-suite failure.

### Complete upstream CI suite

Command copied from `.github/workflows/ci.yml`:

```powershell
uv run pytest -m "" --cov=src/zotero_arxiv_daily --cov-report=term-missing
```

Result:

- 83 collected.
- 80 passed.
- 3 failed.
- Total coverage: 87% (700 statements, 91 missed).
- Runtime: 610.94 seconds.

Failing tests:

1. `tests/reranker/test_local_reranker.py::test_local_reranker`
2. `tests/retriever/test_arxiv_retriever.py::test_run_with_hard_timeout_returns_value`
3. `tests/retriever/test_arxiv_retriever.py::test_run_with_hard_timeout_returns_none_on_failure`

No test was deleted, skipped, weakened, or changed.

## Systematic-debugging findings

### Windows hard-timeout failures

The implementation chooses `fork` when available and otherwise uses the first supported multiprocessing method. Windows reports only `spawn`. `result_queue.get(timeout=1)` begins immediately after `process.start()`, so the one-second test budget includes interpreter startup and complete re-import of the test/module dependency graph.

The failure reproduced consistently in the full default suite and in an isolated two-test command. A repository-external diagnostic script measured:

- 1.0-second limit: timed out after 1.031 seconds.
- 2.0-second limit: timed out after 2.040 seconds.
- 5.0-second limit: returned `"done"` after 2.785 seconds.
- 5.0-second exception path: returned `None` after 2.815 seconds and logged `RuntimeError: boom` correctly.

Root cause: the tests assume fork-like subprocess startup and do not allow Windows spawn/import overhead. The production PDF/tar timeouts are 180 seconds, so this baseline failure does not prove those production operations always fail; it does prove the one-second tests are not cross-platform. Stage 0 intentionally does not alter core source or tests.

### Slow local-reranker failure

The slow test creates `SentenceTransformer` for `jinaai/jina-embeddings-v5-text-nano-retrieval`. During the full run, Hugging Face metadata/model access exhausted internal retries with `httpx.ConnectTimeout` / Windows error 10060, and no complete cached model was available. A later unauthenticated HTTP HEAD to the public model page returned 200, so the endpoint is not permanently unavailable and no Hugging Face credential is required; the observed failure is a transient/large-model network dependency plus cache miss.

Stage 0 did not retry the entire ten-minute full suite merely to change the recorded outcome.

## Lint and type checking

No Ruff, Flake8, Black, Mypy, Pyright, or basedpyright dependency/configuration exists in `pyproject.toml`, and upstream `CLAUDE.md` explicitly states that no linter or formatter is configured.

- Lint command: not configured upstream.
- Type-check command: not configured upstream.
- Result: not run because there is no authoritative command or configuration; no substitute command was invented.

Adding those tools is a future quality-stage decision and must begin with an approved plan rather than changing the Stage 0 baseline.

## Network, credentials, and cost classification

### Secret scan and inherited documentation examples

The Stage 0 tracked/pending-file scan initially detected three secret-shaped example values in upstream `assets/use_docker.md`: Zotero key, sender password, and OpenAI-compatible API key examples. Their values were suppressed during diagnosis. The current branch replaces them with `${ZOTERO_KEY}`, `${SENDER_PASSWORD}`, and `${OPENAI_API_KEY}` references, and the same scan then passed across all tracked/pending text files.

The original strings remain in the inherited public upstream Git history at commit `921378562e98b8f1848f5c502967df08a2064cba`; Stage 0 does not rewrite upstream history or force-push. They are treated as potentially sensitive examples because their validity cannot be proven or disproven locally. No credential introduced by this project exists in the current tree or pending diff.

### Default tests

The default suite uses fakes/monkeypatching for Zotero, arXiv API, OpenAI-compatible calls, and SMTP behavior. It requires no real key and should be offline apart from dependency import behavior.

### Full test suite

The one slow test accesses Hugging Face and may download the configured embedding model. It uses no paid API but requires public network access, time, cache space, and sufficient memory.

### Real application run

The current upstream pipeline would require:

- `ZOTERO_ID` and `ZOTERO_KEY` for Zotero.
- `OPENAI_API_KEY` and `OPENAI_API_BASE` for TLDR/affiliation calls.
- `SENDER`, `RECEIVER`, and `SENDER_PASSWORD` for SMTP.
- Public network access to Zotero, arXiv RSS/API/source/HTML/PDF, the chosen embedding source, the configured LLM endpoint, and SMTP.

The new `.env.example` uses the product-facing names `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`; adapting those names to the upstream `OPENAI_*` configuration belongs to a later implementation stage. Feishu variables are documented for the future client but unused by upstream.

Potentially paid operations:

- OpenAI-compatible chat completions for TLDR/affiliations.
- API embedding reranker if configured with a paid provider.
- Future LLM reranking and full analysis.

The local embedding reranker, pytest mocks, Stage 0 inspection, and HTTP HEAD diagnosis do not incur API fees. Stage 0 made no LLM, Zotero, SMTP, or Feishu call.

## Upstream architecture findings

### Entry and orchestration

- `src/zotero_arxiv_daily/main.py` is the Hydra entry point and configures Loguru output.
- `Executor` performs a linear flow: Zotero fetch/filter → source retrieval → reranking → TLDR/affiliation generation → email rendering/sending.
- README's local example says `uv run main.py`, while the actual packaged path and `CLAUDE.md` command are `uv run src/zotero_arxiv_daily/main.py`. Use the latter.

### Zotero client

- `Executor.fetch_zotero_corpus` constructs `pyzotero.zotero.Zotero` directly.
- It retrieves all collections/items, resolves recursive collection paths, and creates `CorpusPaper` values.
- `filter_corpus` already implements multi-pattern includes and exclusion precedence.
- There is no explicit application cache, gateway interface, request timeout, or retry policy around Zotero.

### arXiv and other sources

- Retriever classes use a registry around `BaseRetriever`.
- arXiv reads RSS, retrieves batches through `arxiv.Client(num_retries=10, delay_seconds=10)`, and handles HTTP 429 with bounded increasing waits.
- Conversion eagerly attempts tar, then HTML, then PDF extraction for every retrieved arXiv paper. This conflicts with the desired rank-before-PDF cost boundary and requires adaptation.
- PDF/source downloads use connect/read timeout `(10, 60)` and 180-second subprocess hard limits.
- bioRxiv retries ten times with fixed sleeps but its `requests.get` currently has no explicit timeout.
- `BaseRetriever.retrieve_papers` isolates conversion errors per paper and adds a one-second delay.

### Reranking

- `BaseReranker` computes weighted mean similarity against Zotero abstracts, giving more recent Zotero additions higher weight.
- Local and OpenAI-compatible API rerankers share a registry.
- The local model is loaded on each reranker execution; embeddings and model output are not persisted by application code.
- The API reranker batches embeddings but has no application-level cache.

### PDF, LLM, and email

- `utils.extract_markdown_from_pdf` returns a single Markdown string via pymupdf4llm, without durable page/block/Figure/Table mappings.
- `Paper.generate_tldr` and `generate_affiliations` call the LLM directly and mutate dataclass fields.
- TLDR prompts may include full text truncated to 4,000 tokens; affiliations use up to 2,000 tokens.
- LLM failures fall back to the abstract or `None`; responses are not cached.
- Email HTML is generated by `construct_email.py`.
- SMTP tries STARTTLS, then SSL, then plain SMTP. No explicit SMTP timeout is configured.

### Logging and error handling

- The application uses Loguru on stdout with INFO/DEBUG selection.
- Per-paper retrieval/conversion and LLM failures are often logged and skipped/fallback safely.
- The workflow writes `CUSTOM_CONFIG` and prints `config/custom.yaml`; this is safe only when it contains environment references, and is a future leakage risk if a user places literal secrets in the variable.
- No structured run manifest or per-stage status exists.

### Caching

There is no application-level persistent cache for Zotero data, arXiv metadata, embeddings, PDFs, parsed documents, or LLM responses. Temporary directories isolate tar/PDF extraction. uv and Hugging Face maintain tool/library caches outside the application's data model.

## Reuse classification

### Directly reusable

- Retriever/reranker registries and abstract bases.
- `BaseReranker` weighted-similarity scoring core.
- `glob_match` and its test suite.
- Existing Hydra/OmegaConf configuration composition core.
- Existing regression tests for email and current pipeline behavior.

### Adapt without rewriting core behavior

- Zotero fetch/filter into an injected interest-provider interface.
- arXiv retrieval into metadata-only retrieval before PDF work.
- Local/API rerankers with structured records and caches.
- OpenAI-compatible endpoint configuration behind product-facing `LLM_*` variables and an injected client boundary.
- pymupdf4llm extraction behind a page-aware parser.
- Executor orchestration into staged/cost-bounded composition.
- GitHub Actions with least-privilege and secret-safe output.

### New

- Candidate store and selection audit.
- Page/section/caption/document mapping.
- Evidence extraction/validation and strict schemas.
- Structured Chinese analysis.
- Direct Feishu client.
- Read/irrelevant feedback and ranking projection.
- Versioned application caches, run manifests, and cost metrics.

## Licensed Hermes source audit

The project owner confirms the local Hermes archive is licensed for use, modification, adaptation, and reuse in this project.

| Item | Value |
|---|---|
| Archive | `F:\Yan_0\Video_generaton\PaperDaily\hermes-arxiv-agent-main.zip` |
| SHA-256 | `5456921C484DF0F6BE35DB07A60CA7D9B25E40A913EDDC5BD9269D830AAB42A6` |
| Extracted audit copy | `F:\Yan_0\Video_generaton\PaperDaily\hermes-reference\hermes-arxiv-agent-main` |
| Git relationship | No submodule and no merged history |
| Project permission basis | Project-owner licensed-source authorization recorded in the approved design |

Implementation findings:

- `monitor.py` queries the arXiv export API (maximum 50), immediately downloads each new PDF, tracks crawled/pending IDs, writes Excel/JSON, and asks the Hermes cron agent to fill affiliations/Chinese summaries.
- arXiv search and PDF requests have 30/60-second timeouts but no bounded retry wrapper.
- `new_papers.json` carries date/counts, Excel/paper paths, `new_papers`, `papers_to_process`, and `feishu_msg`.
- Chinese summaries are 90–150 characters based on Abstract and cover method/contribution/result. This format is reusable for compact digests but does not satisfy full-PDF evidence requirements by itself.
- Feishu delivery is performed by Hermes cron conversation delivery. The archive provides message composition/data examples but no standalone Feishu application API client.
- `viewer/build_data.py` converts Excel rows into static JSON, deduplicates by arXiv ID using information quality, and sorts by crawl/publication date.
- `viewer/app.js` supports date/keyword/favorite filters and stores favorites under `hermes-arxiv-agent:favorites` in browser `localStorage`.
- Local serving also supports `viewer/favorites.json` through a small HTTP API.
- No read or irrelevant state exists.
- Pages workflow uploads the complete `viewer` directory and injects an optional analytics token.

Observed data formats:

- `excel_data.json`: object keyed by arXiv ID; values contain title, authors, affiliations, publication date, Abstract, and Chinese summary.
- `feishu_output.json`: array items contain arXiv ID, title, authors, affiliations, date, Chinese summary, and PDF URL.
- `llm_results.json`: object keyed by arXiv ID with metadata, local PDF path, affiliations, and Chinese summary.
- `merged_llm_results.json`: object keyed by arXiv ID with affiliations, Chinese summary, and Chinese character count.
- `viewer/papers_data.json`: root count/date-range metadata plus a paper array.

Licensed Hermes components suitable for later reuse/adaptation:

- Static viewer layout, filtering/search, favorite state, local server, and Pages workflow.
- JSON/Excel field mapping and deduplication approach.
- Chinese digest/message composition.
- Pending/crawled state concepts.

Sample JSON, generated summaries, images, downloaded-paper paths, and local state are not Stage 0 product data and are not copied into Git.

## Local operation

After uv is available:

```powershell
uv sync --frozen
uv run pytest
uv run src/zotero_arxiv_daily/main.py
```

The application command requires real environment configuration and performs network/LLM/email operations. Do not run it as a Stage 0 smoke test. Configure future secrets locally or in GitHub Secrets; never place them in chat or tracked YAML.

## Upstream update procedure

Use non-destructive inspection and merge; never push to `upstream`:

```powershell
git fetch upstream --prune
git log --oneline --decorate HEAD..upstream/main
git diff --stat HEAD...upstream/main
```

After reviewing changes and ensuring the working tree is clean, update through a dedicated reviewed synchronization branch or merge `upstream/main` into the current personal branch:

```powershell
git switch -c chore/sync-upstream-YYYY-MM-DD
git merge --no-ff upstream/main
uv sync --frozen
uv run pytest
```

Replace the date in the branch name with the actual synchronization date. Resolve conflicts deliberately, rerun all configured tests, and push only to the private `origin`. Do not force-push or rewrite upstream history.

## Known issues and Stage 0 conclusions

- GitHub CLI is not authenticated, so private `origin` creation/push is unavailable in Stage 0 unless authentication changes before finalization.
- Default tests have two reproducible Windows spawn-timeout failures.
- Full CI adds one transient Hugging Face network/cache failure.
- No lint or type-check baseline exists.
- System Python 3.11 cannot satisfy the project; Python 3.13/uv are hard requirements for the current lock/build configuration.
- The project has no persistent expensive-call cache.
- Current arXiv conversion performs full text extraction before reranking, contrary to the product cost boundary.
- Current PDF representation cannot bind pages, sections, figures, tables, or ablations.
- Current LLM output is free-form TLDR/affiliation data rather than validated structured analysis.
- Current output is email only; Feishu, complete static analysis, and feedback require later roadmap stages.
- The current branch redacts three upstream secret-shaped Docker-documentation examples; inherited upstream history still contains the original public blob and has not been rewritten.

These are reported baseline facts, not hidden failures and not Stage 0 implementation work.

## Stage 1 completion notes (2026-07-20)

Stage 1 was implemented on `feat/stage-1-interest-candidates` in the isolated `.worktrees/stage-1-interest-candidates` worktree. No real Zotero credentials, paid API, PDF download, LLM call, email, Feishu, static viewer, or feedback feature was used or added.

Implemented boundaries:

- Strict Pydantic schemas for privacy-safe interest records, metadata-only arXiv candidates, ranking provenance, actual selection limits, counts, and deterministic candidate batches.
- Production and fakeable Zotero interest providers for the approved include/exclude paths, with bounded retry, explicit HTTP timeouts, item/collection isolation, and privacy-safe issue reporting.
- Metadata-only arXiv retrieval for `cs.CV`, `cs.LG`, and `cs.AI`, including stable ID/version normalization, deduplication, malformed-entry isolation, cross-list policy, explicit timeouts, and bounded `Retry-After` handling.
- Deterministic weighted-cosine ranking with stable tie ordering, local SentenceTransformer production embeddings, and a versioned `.npy` plus JSON-manifest cache keyed by complete embedding identity and text hash.
- Atomic, schema-validated candidate JSON persistence under the ignored `data/candidates/` boundary.
- A production composition path using environment-only `ZOTERO_ID`/`ZOTERO_KEY`, merged base/custom configuration, local embeddings, ignored cache/store paths, and resource cleanup.
- A credential-free offline fixture command whose dry-run performs no candidate or embedding-cache write:

```powershell
uv run python -m zotero_arxiv_daily.pipeline.candidates --dry-run --offline-fixture tests/fixtures/stage1_offline.json
```

Final verification evidence:

- Stage 1 suite: `75 passed`.
- Default regression suite: `155 passed`, `2 failed`, `1 deselected`; both failures are the unchanged Windows one-second multiprocessing spawn-timeout baseline failures.
- Complete configured suite: `155 passed`, `3 failed` in 542.59 seconds with `90%` total coverage. The third failure is the unchanged slow local-reranker Hugging Face connection/cache dependency; the model could not be downloaded and was absent locally.
- Offline fixture CLI: exit code `0`; no credential variables, candidate output directory, or embedding cache required/created.
- No authoritative lint or static type-check command exists, so none was invented.
- `git diff --check` passed. Representative candidate, embedding-cache, `.env`, and `.env.*` paths are ignored; `.env.example` remains trackable.
- No tracked PDF/ZIP, candidate/cache artifact, private Zotero export, file over 5 MiB, or common secret-shaped value was found.
- Independent Superpowers code review initially found production composition, malformed-record, cache identity, limit, retry, dry-run, task-provenance, UTC, and run-ID issues. All confirmed Critical/Important findings were fixed with regression tests. Final review verdict: `Ready to merge? Yes`.

The Stage 1 production path was composition-tested with injected fakes only. A real Zotero/arXiv run remains intentionally unexecuted until the user configures credentials locally in a later session. At that Stage 1 snapshot, Stage 2 PDF extraction had not begun; its completed baseline follows.

## Stage 2 completion notes (2026-07-20)

Stage 2 was implemented on `feat/stage-2-pdf-documents` in the isolated `.worktrees/stage-2-pdf-documents` worktree. The stage starts from Stage 1 commit `7c1036a`. No real Zotero credentials, paid API, LLM call, email, Feishu, viewer, feedback feature, or GitHub Actions workflow was used or added.

Environment and parser baseline:

- Python `3.13.14`, managed through the existing local uv executable.
- Docling `2.113.0` for layout-aware document conversion.
- PyMuPDF `1.27.2.2` for bounded inspection and evidence-region rendering.
- ReportLab `5.0.0` only for runtime-generated test fixtures; no PDF binary is tracked.
- Docling artifacts must already exist at the configured local artifacts path. Stage 2 validation did not download a model or invoke a remote/paid service.

Implemented boundaries:

- A strict Stage 2 document schema covering physical PDF pages, text blocks, coordinates, source provenance, section hierarchy, Figure/Table regions, captions, evidence-image paths, structured issues, confidence, parser/config versions, and processing status.
- Selection gating that preserves Stage 1 metadata-only ranking and downloads only `selected_for_full_analysis` papers, capped at five.
- A content-addressed arXiv PDF downloader with an exact HTTPS host allowlist, userinfo rejection/redaction, validation of every redirect target, explicit timeouts, finite retries, bounded numeric or HTTP-date `Retry-After`, response/cache size limits, content-length/signature/media-type checks, atomic writes, and partial-file cleanup.
- Pre-parser PDF inspection with explicit statuses for malformed PDFs, empty documents, missing text layers/scanned pages, mixed text layers, and unsupported/encrypted inputs. One paper fails independently without aborting its batch.
- A lazy Docling adapter with a fixed local artifact boundary, document timeout, remote services disabled, external plugins disabled, and the installed parser version recorded in every graph.
- Page-aware mapping for text blocks, sections, captions, figures, and tables. Graph validation enforces page/bbox resolution, parser identity, cycle freedom, bidirectional section membership, exact section member-page boundaries, caption provenance, evidence-root containment, and success/error consistency.
- Figure/Table values remain `null` or carry structured issues when a label, caption, provenance mapping, or crop cannot be established. No fallback invents a label, caption, section title, or evidence claim.
- Versioned graph caching by PDF hash plus parser/mapper/config versions. Cache reads reject corrupt JSON, mismatched versions, external evidence roots, missing/corrupt PNG files, and partial parse results. Evidence images are atomically rendered under the ignored cache boundary.
- An injected, offline Stage 2 pipeline test path from a Stage 1 candidate batch through selection, safe download, inspection, conversion, mapping, evidence rendering, and result isolation.

Verification commands:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' sync --frozen
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/analysis/test_document_schemas.py tests/documents tests/pipeline/test_documents.py -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/analysis/test_stage1_schemas.py tests/interest tests/candidates tests/retriever/test_arxiv_metadata.py tests/pipeline/test_candidates.py -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest -m "slow or not slow" -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run python -m compileall -q src
```

Verification evidence:

- Frozen dependency sync: `172 packages` checked successfully.
- Stage 2 suite: `64 passed`.
- Stage 1 regression suite: `75 passed`.
- Default regression suite: `219 passed`, `2 failed`, `1 deselected`; both failures are the unchanged Windows one-second multiprocessing spawn-timeout baseline failures.
- Complete configured suite: `219 passed`, `3 failed` in `76.11 seconds` on the final run. The third failure is the unchanged slow local-reranker Hugging Face connection/cache dependency; the model could not be downloaded and was absent locally. An earlier run with the same failure set took `506.78 seconds`, so this network-dependent duration is not stable.
- Source compilation and `git diff --check` passed. No authoritative lint or static type-check command exists, so none was invented.
- No tracked secret, PDF/ZIP, private Zotero data, parser/LLM cache, local reading state, or file over 5 MiB was found. Representative runtime paths remain ignored.
- Independent Superpowers review found and drove fixes for SSRF/redirect handling, cache reads and containment, parser timeouts/version identity, graph invariants, caption provenance, retry semantics, corrupt images, and partial-result caching. After all confirmed Critical/Important findings were fixed with tests, the final verdict was `Ready: Yes`.

Known limits:

- Scanned or image-only papers are reported as explicit failure/partial states; Stage 2 does not add OCR.
- Cross-page captions and complex multi-page tables are retained only when Docling provenance resolves them; otherwise the corresponding fields remain `null` with structured issues.
- Docling cold import/model initialization is materially slower than PyMuPDF inspection, so conversion is lazy and exact-version graph cache hits occur before Docling is loaded.
- Acceptance used deterministic runtime-generated fixtures and injected network/parser doubles. No real paper PDF was committed or required.

## Stage 3 completion notes (2026-07-20)

Stage 3 was implemented on `feat/stage-3-structured-analysis` in the isolated `.worktrees/stage-3-structured-analysis` worktree, starting from Stage 2 commit `b2ad648`. No real Zotero or LLM credential, real paper, paid API, email, Feishu, viewer, feedback feature, or GitHub Actions workflow was used or added. Credentials pasted into chat were not read from the environment, tested, stored, logged, or committed and must be revoked and rotated by the project owner.

Environment and structured-analysis baseline:

- Python `3.13.14`; frozen dependency sync checked `172 packages`.
- Pydantic `2.12.5` provides frozen, `extra="forbid"` schemas.
- OpenAI SDK `2.29.0` is isolated behind an injected `StructuredAnalysisClient`; acceptance uses only fake clients.
- `stage3-v1` is the initial immutable prompt identity; analysis and evidence packet schemas are version `1.0`.
- The repository still has no authoritative lint or static type-check command, so none was invented.

Implemented boundaries:

- Strict schemas cover the English title, Chinese title, recommendation, research problem, Insight and formation logic, supporting Figure/Table explanation, Method overview/modules, prior-work differences, parameter symbol/role/value/selection/tuning, ablations, experimental conclusions, limitations, links, evidence candidates, generation metadata, issues, and batch metrics.
- Every claim records `author_statement`, `system_summary`, or `system_inference`; only the last permits `inferred=true`. Schema validators bind each claim kind to its output field.
- Content evidence is projected only from the Stage 2 `DocumentGraph`. Candidate metadata supplies only paper identity, English title, and canonical PDF/arXiv/code links; candidate Abstract and Zotero fields never enter the analysis prompt.
- Evidence packets deterministically prioritize Introduction/Motivation/Observation/Analysis, Method, Experiments, Ablation, Appendix, other sections, then Abstract. They retain PDF page, section path, bbox, image path, complete `SourceMapping`, confidence, label, and full Stage 2 caption provenance.
- Evidence IDs include PDF hash, evidence kind, source item, physical page, and bbox. The prompt sends only bounded evidence text; visual captions use the budgeted projection while final records materialize the original Stage 2 caption and provenance.
- A full-Abstract evidence packet is rejected before any client call. Each successful Insight has non-Abstract evidence. An explicitly missing Insight produces `partial`, not `success`, and partial/failed results are never written to or reused from the expensive-call cache.
- The prompt includes the strict draft JSON Schema for providers that support only JSON-object response mode. Responses have a byte limit, strict JSON/Pydantic parsing, safe error codes, explicit HTTP timeout, SDK retries disabled, and analyzer-controlled finite retry/backoff.
- Successful analysis cache identity includes PDF hash; DocumentGraph schema/parser/mapper/config/content identities; packet schema/builder/content identity; prompt/schema/model/generation identities; and a fingerprint of the prompt-visible title and links. Writes are atomic and validated; corrupt, oversized, mismatched, partial, or stale entries are misses.
- The batch pipeline preserves `selected_for_full_analysis` order, processes at most five papers, keeps at most three visual evidence records per paper, rejects duplicate or mismatched Stage 2 results, isolates per-paper failures, and counts expensive work per paper rather than per retry attempt.
- A production composition function reads `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` only from an explicitly injected environment in the non-dry-run path. Missing-variable errors contain names but never values.
- The artificial `tests/fixtures/stage3_offline.json` dry-run builds a DocumentGraph evidence packet, prompt, strict draft, analysis, and batch result with a fake client, zero expensive calls, no credential reads, no network, and no filesystem cache or output write:

```powershell
uv run python -m zotero_arxiv_daily.pipeline.analysis --dry-run --offline-fixture tests/fixtures/stage3_offline.json
```

Final verification commands:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' sync --frozen
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/analysis/test_paper_schemas.py tests/analysis/test_prompt.py tests/analysis/test_client.py tests/analysis/test_analysis_cache.py tests/analysis/test_analyzer.py tests/analysis/test_stage3_offline.py tests/documents/test_evidence.py tests/pipeline/test_analysis.py -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/analysis/test_document_schemas.py tests/documents tests/pipeline/test_documents.py -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/analysis/test_stage1_schemas.py tests/interest tests/candidates tests/retriever/test_arxiv_metadata.py tests/pipeline/test_candidates.py -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest -m "slow or not slow" -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run python -m compileall -q src
```

Fresh verification evidence:

- Frozen dependency sync: `172 packages` checked successfully.
- Stage 3 focused suite: `71 passed`.
- Stage 2 regression suite: `69 passed`.
- Stage 1 regression suite: `75 passed`.
- Default regression suite: `290 passed`, `2 failed`, `1 deselected` in `9.66 seconds`. Both failures are the unchanged Windows one-second multiprocessing spawn-timeout baseline failures.
- Complete configured suite: `290 passed`, `3 failed` in `498.63 seconds`. The third failure is the unchanged slow local-reranker Hugging Face connection/cache dependency: `jinaai/jina-embeddings-v5-text-nano-retrieval` could not be reached and was absent from the local cache.
- Offline fixture CLI returned one success with `expensive_call_count=0` and `cache_hit_count=0`; it created no cache directory.
- Source compilation and repository diff checks passed. No authoritative lint or static type-check command exists.
- Hygiene scans found no tracked secret, PDF/ZIP, private Zotero data, candidate/document/analysis cache, local viewer state, or file over 5 MiB. `.env.example` remains trackable and representative secret/cache/PDF/private-data paths remain ignored.

Independent Superpowers review initially found five Important issues: the response Schema was not sent to the model, empty/incorrect claim structures could be marked successful, prompt-visible metadata was absent from cache identity, full captions bypassed the prompt budget, and bbox was absent from evidence IDs. RED-to-GREEN fixes addressed all five. A second review found partial analysis caching; the final fix makes partial/failed entries misses. Final independent verdict at commit `4bba9a3`: `Ready`, with no unresolved Critical or Important findings.

Known limits and next boundary:

- Stage 3 checks strict structure and reference-set integrity, but does not decide whether the cited text or Figure/Table semantically proves a claim, verify reported numbers against the document, or determine publication eligibility. Those are Stage 4 responsibilities.
- Missing parameters, ablations, limitations, links, or other paper-absent facts remain `None`/empty tuples rather than being invented. A missing core Insight downgrades the result to `partial`.
- Prompt JSON Schema is embedded for broad OpenAI-compatible JSON-object support. Provider-specific native structured-output optimization is deferred until it can preserve the approved injectable-client contract.
- Acceptance is deterministic and zero-cost. A real model run remains intentionally unexecuted and is not required for Stage 3 completion.

Stage 4 evidence validation and hallucination prevention is complete. Stage 5
and Stage 6 completion notes follow.

## Stage 4 completion notes (2026-07-20)

Stage 4 was implemented on `feat/stage-4-evidence-validation` in the isolated
`.worktrees/stage-4-evidence-validation` worktree, starting exactly from Stage 3
commit `8603098474db25826de88af407bdfffb00cdb2b7`. No real Zotero or LLM
credential, real paper, paid API, paper/model download, email, Feishu, viewer,
feedback feature, GitHub Actions workflow, push, or pull request was used or added.

Implemented boundaries:

- Strict `ValidationIssue`, `ClaimValidationResult`, `ValidationReport`,
  `ValidatedPaperAnalysis`, `ValidationPaperResult`, and `ValidationBatchResult`
  schemas distinguish valid, partial, invalid, failed, and skipped outcomes. Only
  an error-free valid report is publication-eligible.
- Validation consumes the unchanged Stage 2 `DocumentGraph`, Stage 3
  `EvidencePacket`, and Stage 3 `PaperAnalysis`. Candidate metadata is restricted
  to paper identity, English title, and canonical PDF/arXiv/code links.
- Text evidence is bound to an allowed source block type and a deterministic
  prefix of the source block. Evidence IDs and packet fingerprints are recomputed;
  synchronized packet/analysis tampering does not bypass provenance checks.
- Figure/Table identity, label, caption, page, section hierarchy, bbox, image path,
  complete `SourceMapping`, regions, and confidence must match the document graph.
  Supporting visuals bind existing Insights and include a structured support claim.
- Claims reject missing or duplicate evidence, Abstract-only Insight support,
  wrong field kinds, and inconsistent `source_type`/`inferred` flags. Parameters
  and ablations reject dangling references; every ablation conclusion cites its
  declared real Figure/Table evidence.
- Stage 3 partial results remain partial and blocked. Serious evidence errors are
  invalid and blocked. Missing paper-absent optional facts remain `None`/empty and
  do not become hallucination issues. Invalid analysis is not rewritten or exposed
  through the strict validated-analysis wrapper.
- Validation cache identity includes candidate, PDF/document/parser/mapper/config,
  packet/builder, analysis/schema/generation, Stage 3 status, validator version,
  and validation schema identities. Reads recompute analysis and report content
  fingerprints; corrupt, oversized, stale, or valid-JSON-tampered entries are misses.
- Batch composition handles at most five selected papers, preserves order, converts
  run-ID mismatches and per-paper identity/cache/validator exceptions into fixed
  public-safe issues, and continues with later papers. Error messages are bound to
  controlled codes and cannot contain arbitrary URLs, credentials, paths, paper
  text, prompts, or model responses.
- The artificial `tests/fixtures/evidence/stage4_golden.json` command validates one
  eligible paper entirely offline with a no-write cache:

```powershell
uv run python -m zotero_arxiv_daily.pipeline.validation --dry-run --offline-fixture tests/fixtures/evidence/stage4_golden.json
```

Final verification commands:

```powershell
uv sync --frozen
uv run pytest tests/analysis/test_validation_schemas.py tests/analysis/test_validator_identity.py tests/analysis/test_validator_rules.py tests/analysis/test_validation_cache.py tests/pipeline/test_validation.py tests/analysis/test_stage4_offline.py -q
uv run pytest tests/analysis/test_paper_schemas.py tests/analysis/test_prompt.py tests/analysis/test_client.py tests/analysis/test_analysis_cache.py tests/analysis/test_analyzer.py tests/analysis/test_stage3_offline.py tests/documents/test_evidence.py tests/pipeline/test_analysis.py -q
uv run pytest tests/analysis/test_document_schemas.py tests/documents tests/pipeline/test_documents.py -q
uv run pytest tests/analysis/test_stage1_schemas.py tests/interest tests/candidates tests/retriever/test_arxiv_metadata.py tests/pipeline/test_candidates.py -q
uv run pytest -q
uv run pytest -m "slow or not slow" -q
uv run python -m compileall -q src
git diff --check 8603098474db25826de88af407bdfffb00cdb2b7..HEAD
```

Fresh verification evidence:

- Frozen dependency sync checked `172 packages` successfully.
- Stage 4 focused suite: `103 passed`.
- Stage 3 regression suite: `71 passed`.
- Stage 2 regression suite: `69 passed`.
- Stage 1 regression suite: `75 passed`.
- Default suite: `393 passed`, `2 failed`, `1 deselected` in `16.42 seconds`.
  Both failures are the unchanged Windows one-second multiprocessing spawn-timeout
  baseline failures.
- Complete configured suite: `393 passed`, `3 failed` in `509.42 seconds`. The
  third failure is the unchanged slow local-reranker dependency:
  `jinaai/jina-embeddings-v5-text-nano-retrieval` could not be reached and was not
  present in the local Hugging Face cache.
- Offline golden CLI returned one valid and eligible result, zero partial/invalid/
  failed/skipped results, and zero cache hits. Source compilation and repository
  diff checks passed. The repository has no authoritative lint or static type-check
  command, so none was invented.
- A scan of `159` tracked files found `0` common secret-shaped values, `0` tracked
  PDF/ZIP files, `0` private/cache/viewer-state artifacts, and `0` files over
  5 MiB. The only tracked binary-image matches are the existing small documentation
  PNG assets. Representative `.env`, paper, Zotero, candidate/document/analysis/
  validation cache, and viewer-state paths remain ignored; `.env.example` remains
  trackable.

Independent review history:

- The first review found cache reuse across Stage 3 status changes, text evidence
  body/ID gaps, valid-JSON cache tampering, batch exception leakage, incomplete
  ablation binding, schema-bypass invariants, a weak validated-analysis boundary,
  and uncontrolled issue messages. All confirmed findings were reproduced with
  failing tests and fixed.
- The second review reproduced a numbered Abstract/Summary eligibility bypass,
  unresolved section cycles/dangling parents, and a combined paper-ID/claim-error
  exception. Stage 2 and Stage 4 now share one Abstract classifier; invalid section
  hierarchies are explicit provenance errors; rule issues use the trusted Candidate
  paper ID.
- The third review confirmed those bypasses closed and found one remaining unsafe
  dynamic section location. Commit `df136d8` replaced it with a stable index path
  and added direct-validator regressions for unsafe section IDs. The final short
  review of commit `13d79bb` returned `Ready to merge? Yes`, with no unresolved
  Critical or Important findings.

Known limits and rollback:

- Stage 4 proves deterministic reference and provenance integrity; it does not add
  OCR, VLM, chart-value recognition, a new parser, or a second LLM analysis flow.
  Indirect semantic entailment beyond the structured evidence rules remains bounded
  by the Stage 2 mapping and Stage 3 evidence packet.
- No corrective retry was implemented, so validation has zero model/network cost and
  cannot authorize new evidence IDs.
- Roll back by disabling downstream publication of new analyses, or revert the
  Stage 4 commits together with validation cache/schema version changes. Retain Stage
  2 graphs and Stage 3 analyses for later revalidation; never bypass the validator.

## Stage 5 completion notes (2026-07-21)

Stage 5 was implemented in the isolated `feat/stage-5-static-viewer` worktree from
Stage 4 commit `2fb1d09b18d6f66ed808c1e41567bae4c14e1407`. It adds only an offline
static reader; it does not add Stage 6 delivery, Stage 7 automation, Stage 8
feedback/localStorage, a server, a paid API, a real-paper run, a push, or a pull
request.

Implemented boundary:

- `PublicationPolicy` permits full pages only for Stage 4 `valid` and eligible
  results; partial pages require an explicit setting. Invalid, failed, skipped, and
  blocked pages are excluded, and a rebuild removes stale generated detail pages.
- The page builder writes escaped semantic index/detail HTML, strict local CSP,
  responsive CSS, local favicon, manifest, and a fixture-only CLI. No CDN, script,
  browser storage, external API, or dynamic server is used.
- Evidence images are accepted only from configured local roots, decoded/size/type checked,
  content-addressed under `assets/evidence/`, and rendered with local `img`/`alt`.
  Missing or rejected images become a structured local fallback with no network
  retry. Output and asset paths reject traversal and symbolic-link routing.
- The checked Hermes directory had no license file; its source code, data, images,
  cache, and Git history were not copied or adapted.

Fresh verification evidence:

- Frozen dependency sync checked `172 packages` successfully.
- Stage 5 plus Stage 4 focused tests: `136 passed`.
- Stage 5 viewer suite: `33 passed`.
- `compileall`, `git diff --check`, and the offline fixture build passed.
- Default suite: `426 passed`, `2 failed`, `1 deselected` in `15.98 seconds`; both
  failures are the existing Windows one-second multiprocessing spawn-timeout tests
  under `tests/retriever/test_arxiv_retriever.py`, unchanged from the Stage 4
  baseline.
- Complete configured suite: `426 passed`, `3 failed` in `508.62 seconds`. The
  additional failure is the known `tests/reranker/test_local_reranker.py` attempt
  to obtain the absent `jinaai/jina-embeddings-v5-text-nano-retrieval` model from
  Hugging Face, which timed out on this host. No Stage 5 code was changed to hide
  these environment-dependent baseline failures.
- Playwright local-server smoke testing found zero console errors on index and
  detail pages; at 390px the detail page had `scrollWidth == clientWidth == 390`.
- Hygiene scan found `0` common secret-shaped values, `0` tracked private/cache/
  output paths, `0` tracked PDF/archive/database files, and `0` tracked files over
  5 MiB. The generated viewer output and Playwright artifacts are ignored.
- A second independent re-review reproduced and verified fixes for Windows
  drive/UNC/backslash output escapes, CLI evidence-root wiring, and corrupt-image
  rejection. It returned `Ready`; it also reconfirmed stale-page removal,
  output/asset symlink defenses, and credential-bearing HTTPS URL rejection.

Rollback: stop the viewer CLI, remove the ignored configured output directory, and
revert the Stage 5 commits. This leaves Stage 4 validation data and private evidence
roots intact.

## Stage 6 completion notes (2026-07-22)

Stage 6 was implemented in the isolated `feat/stage-6-feishu-delivery` worktree
from Stage 5 commit `aa6c143`. It adds preview-first Feishu digest rendering and
an injectable delivery client. It does not add Stage 7 scheduling, perform a
real Feishu send, call Zotero or an LLM, download a paper/model successfully,
push a branch, or create a pull request.

Implemented boundary:

- Strict frozen delivery schemas reject unknown fields and redact credential-like
  URLs from validation errors, including malformed and whitespace-prefixed input.
- The renderer accepts only Stage 4 `valid` and publication-eligible analyses,
  preserves validated batch order, and includes at most five papers. It emits
  escaped single-line Chinese/English titles, recommendation, one Insight,
  strongest evidence location, experimental conclusion, safe links, and explicit
  missing-data fallbacks.
- Semantic validation-report equality survives JSON hydration; object identity is
  not used as a publication decision.
- Preview mode is the default and constructs no transport. Only the literal
  non-abbreviated `--send` flag can enter delivery, and complete environment-only
  Feishu settings are validated before a transport is constructed.
- The client has explicit timeouts, stable request UUIDs, bounded retries only for
  HTTP 429/5xx, capped numeric or HTTP-date `Retry-After` handling, permanent-4xx
  failure, controlled redacted errors, and an in-process receipt ledger that
  prevents duplicate sends for the same idempotency key.
- Preview writes use a same-directory temporary file, flush/fsync, atomic replace,
  and cleanup. Output/fixture path collisions are rejected. A send run never writes
  a preview artifact.

Fresh verification evidence:

- Frozen dependency sync checked `172 packages` successfully.
- Stage 6 plus Stage 4 focused suite: `151 passed` in `4.07 seconds`.
- Default suite: `474 passed`, `2 failed`, `1 deselected` in `13.68 seconds`.
  Both failures are the unchanged Windows one-second multiprocessing spawn-timeout
  tests in `tests/retriever/test_arxiv_retriever.py`.
- The complete configured suite, run before the five final credential-redaction
  regressions were added, produced `469 passed`, `3 failed` in `421.20 seconds`.
  The additional failure is the known slow local-reranker dependency: the
  configured Jina model was absent locally and Hugging Face metadata access failed
  on this host. The five later tests all pass in the focused suite and do not
  execute the slow model path. Stage 6 does not modify these three failing tests or
  their production code.
- The artificial Stage 4 golden fixture produced one valid Feishu preview with
  `sent=false`. The preview parsed as JSON and matched none of the configured local
  credential values. No Feishu transport or external paid API was invoked.
- Source compilation and the Stage 5-to-Stage 6 diff check passed. The repository
  has no authoritative lint or static type-check command, so none was invented.
- Hygiene checks found no secret-shaped value in tracked product/config/docs files,
  no tracked private/cache/output path, no tracked PDF/archive/database, and no
  tracked file over 5 MiB. Deliberately credential-shaped test sentinels remain only
  in tests that prove redaction. The local `.env` and generated preview are ignored.

Independent review history:

- Component reviews closed renderer hydration/escaping and client retry,
  idempotency, preview-write, CLI flag, and configuration-safety findings.
- Final whole-branch review reproduced credential error leakage when whitespace
  appeared inside userinfo or between a scheme and authority, then when a URL used
  a non-HTTPS or protocol-relative authority. Each finding was fixed through a
  failing regression test before the minimal implementation change.
- The final re-review of commit `0f2f17c` exercised HTTPS, arbitrary valid schemes,
  protocol-relative URLs, mixed case, custom schemes, and whitespace in the scheme,
  authority, and userinfo. It returned `Ready` with no unresolved Critical or
  Important finding.

Rollback: disable the Stage 6 CLI/send composition and retain the offline preview
and Stage 5 static reader. Revert the Stage 6 renderer/client/schema commits as one
unit. If a destination or credential was exposed, revoke/rotate it outside Git;
never add it to history while attempting recovery.

## Stage 7 completion notes (2026-07-22)

Stage 7 was completed in the isolated
`feat/stage-7-github-actions-automation` worktree from exact Stage 6 commit
`16beba28d84f8e5c19de2246b1f9b346dbe1dd3c`. Stage 6's branch/worktree was not
modified, merged, or removed. No push, pull request, upstream write, repository
visibility change, real GitHub dispatch, Zotero/private-library read, paper/model
download, paid LLM call, Feishu send, or Pages deployment was performed.

Implemented boundary:

- `pipeline.daily` composes injected Stage 1–6 runners under `stage7-v1`, supports
  scheduled/manual/local triggers, isolates paper and target failures, and defaults
  to fixture dry-run/no-send. `--mode live` and the non-abbreviable
  `--send-feishu` are separate exact gates.
- The strict `RunManifest` records controlled stage status/count/cache/retry/partial
  fields, independent static-site and Feishu results, artifact hash, and safe error
  codes. It has no prompt, full-text, Zotero, secret, response-body, traceback, or
  dynamic exception field and is written with same-directory flush/fsync/replace.
- Workflow cache identity binds config, embedding, parser, mapper, prompt, schema,
  validator, viewer, and delivery versions. The JSON summary cache uses a field
  allowlist; corrupt, oversized, stale, tampered, or identity-mismatched entries are
  misses. General Actions cache paths are only `cache/embeddings`,
  `cache/documents`, and `models/docling`. The separate delivery cache is exactly
  `cache/workflow/delivery-ledger.json`; analysis/validation, `.env`, raw Zotero,
  outputs, feedback, and the lock file are excluded. Publication always reruns
  Stage 4.
- Viewer audit rejects traversal, UNC/foreign Windows drives, symlink/junction
  routing, undeclared HTML, private/cache/feedback paths, PDFs, archives, databases,
  scripts, excessive size, and invalid build manifests before hashing/upload.
- Stage 4 results are reduced to an eligible-only typed batch before either target;
  Stage 7 forces `allow_partial=false` and verifies viewer publication count before
  audit. A non-sensitive ledger holds a cross-process lock over check/send/record.
  Actions uses a unique run/attempt cache key plus stable restore prefix to append
  the latest ledger snapshot across default-branch runs. Viewer success survives
  Feishu failure; viewer/core failure or zero publication cannot replace Pages.
- `personal-paper-daily.yml` uses one CLI for schedule/dispatch, minimal permissions,
  global non-cancelling concurrency, timeouts, full-SHA action pins,
  `persist-credentials: false`, default-branch-only manual live/send, safe paths,
  and exact live/send/Pages acknowledgement Variables. Live mode prepares the
  Docling layout/table models, then production preflight rejects missing/incomplete
  artifacts before private network clients are constructed. Pages requires a strict
  RunManifest reread, core-stage check, positive publication count, and a second
  ArtifactAuditor hash/count match. Unsafe legacy write/config workflows were
  removed; `docs/ACTIONS_SETUP.md` distinguishes Secrets from Variables.

Fresh verification evidence:

- `uv sync --frozen`: `Checked 172 packages` successfully using the existing
  isolated uv tool and Stage 7 virtual environment.
- Stage 6/4 pre-change baseline: `151 passed`.
- Default pre-change baseline: `474 passed`, `2 failed`, `1 deselected`; both failures
  are the unchanged Windows one-second multiprocessing spawn-timeout tests.
- Final Stage 7 pipeline/workflow focused suite: `65 passed in 6.55 seconds`.
- Final Stage 4/5/6 validator/viewer/delivery regression: `184 passed in 4.64
  seconds`.
- Default suite: `539 passed`, `2 failed`, `1 deselected` in `18.05 seconds`. Both
  failures are the unchanged Windows one-second multiprocessing spawn tests in
  `tests/retriever/test_arxiv_retriever.py`.
- Complete slow/non-slow suite: `539 passed`, `3 failed` in `507.07 seconds`. The
  additional failure is the known `tests/reranker/test_local_reranker.py` attempt to
  obtain the absent Jina model from Hugging Face; metadata/model access timed out and
  the model was not cached. No test was removed, skipped, or weakened.
- Workflow YAML/static safety validation: `10 passed`; final `compileall` and
  `git diff --check` passed.
- The final offline CLI returned `status=success`, `published=1`, `delivered=0`.
  Strict manifest reread and ArtifactAuditor agreed on a five-file, 6397-byte viewer
  and SHA-256 `0c77ffd4180f8a8818ec730a2085508bee5090e9b5bea1630d25c6453c8a01ef`.
  Content audit found zero credential/prompt/full-text/feedback markers. No live
  factory, paid call, private read, model download, or send was invoked.
- Tracked hygiene found no secret-shaped values, forbidden private/cache/output
  paths (apart from the required empty `.env.example` template), archives/databases,
  or files over 5 MiB. `.env` and generated `outputs/` remain ignored.

Independent review history:

- The first whole-branch review reproduced Docling hosted-runner failure/empty Pages,
  non-durable delivery cache semantics, Stage 4 partial publication, inconsistent
  manifest status, cache-key bypasses, and run-root junction acceptance. Each code
  issue was captured by a failing regression before the minimal fix.
- The second review confirmed Docling preflight/model preparation, Stage 4 filtering,
  state contracts, Pages fail-closed behavior, cache allowlist, junction rejection,
  and local ledger locking. It identified the remaining same-key Actions cache update
  problem.
- The final review verified the unique run/attempt key plus restore-prefix ledger
  chain, global non-cancelling concurrency, and default-branch live boundary. It
  reported zero Critical/Important findings and `Ready to merge? Yes`.

Known limits:

- The workflow cache intentionally excludes unvalidated LLM/validation payloads,
  trading additional live recomputation for a narrower privacy/recovery boundary.
- Docling tools and model repositories are external live dependencies. Their actual
  GitHub-hosted download/runtime was not executed locally; failure is fail-closed.
- GitHub manages Actions cache retention and quota. If the delivery ledger snapshot
  is cleared, keep the send gate disabled and reconcile delivery history before a
  new send; an empty restore is not proof that a payload was never delivered.
- Stage 6 derives a stable Feishu UUID from the idempotency key, but no real Feishu
  service behavior or receipt recovery was tested locally.
- GitHub dispatch, private repository Secrets/Variables, Pages configuration, real
  model preparation, Zotero/LLM execution, Feishu delivery, and Pages deployment
  require external authorization/runtime and were intentionally not acceptance-tested.

Rollback: remove/disable the Stage 7 schedule and exact acknowledgement Variables,
retain manual fixture dry-run and the last audited viewer, and revert Stage 7
workflow/pipeline commits without changing Stage 1–6. Ignored `outputs/daily` and
`cache/workflow` can be removed after retaining any desired non-sensitive manifest
and reconciling delivery history. Clearing the Actions ledger cache while send is
enabled is not a safe rollback. Revoke/rotate any credential exposed outside Git;
credentials previously pasted in chat should be rotated even though Stage 7 never
prints or commits them.

## Stage 8 completion notes (2026-07-26)

Stage 8 is complete in the locally authorized scope. Browser feedback is immediate
same-origin `localStorage`; only an explicit export and local CLI import can reach
the Git-ignored authoritative store under the default `data/private-feedback/`
root. GitHub Pages cannot write back to that local store, and scheduled Actions do
not synchronize private feedback by default.

The optional production store configuration is
`candidate_pipeline.feedback.store_path` in `config/base.yaml` (tracked default:
`null`). Its path is removed before configuration hashing; feedback state, paper-ID
lists, bundles, browser snapshots, migration backups, and credentials are not
configuration-hash inputs. `favorite_delta` is the only feedback setting retained
in that hash. The default delta is `0.05`, capped at `0.10`; strict
`feedback_adjustment` preserves the bounded embedding component separately from
`final_score`. `irrelevant` exact-ID veto occurs before embedding or paid work,
`read` has no ranking effect, and Stage 4 remains the sole publication eligibility
gate. Scorer/projection identity is v3 after the reviewed floating-point boundary
repair.

Final finding-driven checks: the expanded Stage 8 focused group passed `251` tests
in `15.87s`; explicit Stage 7 plus Stage 6/5/4 regressions passed `417` tests in
`18.37s`. Declared private artifact seeds including `.env`, cache/private/Zotero
paths, archives, migration backups, feedback stores/bundles, and browser-state
variants were rejected. The workflow retained explicit cache/upload allowlists,
least permissions, triggers, concurrency, timeouts, SHA pins, the single CLI, and
no feedback path. Compilation, workflow static validation, `git diff --check`, and
the tracked hygiene scan passed. The scan found 215 tracked files, none over 5 MiB,
and no tracked archive/database/PDF/private artifact; credential-shaped matches
were limited to deliberate redaction sentinels in tests, and no values were printed.

Final default `pytest -q` produced `705 passed`, `2 failed`, `1 deselected` in
`29.05s`. Both failures are exactly the known Windows one-second multiprocessing
spawn tests in `tests/retriever/test_arxiv_retriever.py`; no Stage 8 test failed.
The unfiltered `pytest -m "slow or not slow" -q` command produced no summary before
the 600-second safety cap (exit `124` after `604.516s`), recorded as the existing
uncached local-reranker/Hugging Face environment block rather than a pass or skip.

The final artificial fixture daily CLI ran from `aa48d97` with
dry-run/offline/no-send composition and returned one published viewer item, zero
deliveries, and artifact SHA-256
`7ca1c753d2ed4828fabe5f29387c97fd3fc55d88c996c2e67ea5aa039cf92a13`.
ArtifactAuditor accepted exactly six files (32,608 bytes): first-party favicon,
stylesheet, feedback script, build manifest, index, and one fixture paper page.
There were no embedded `.env`, private store/bundle/state, Zotero, cache,
PDF/archive, inline/remote script, or `eval` markers; CSP permits only same-origin
resources and scripts. A synthetic non-real feedback bundle import with `--dry-run`
left the private store absent before and after.

The first independent whole-branch review found 0 Critical, 2 Important and 2 Minor
issues. Each received a focused failing reproduction before repair. Re-review found
one additional Important floating-point boundary issue, which likewise received a
focused RED and versioned repair. Final re-review reported no Critical, Important,
or Minor findings, with Spec Compliance `PASS` and Code Quality `PASS`.

No real GitHub dispatch/Pages deployment, Zotero/private-library access, paper/model
download, paid LLM call, Feishu send, push, PR, merge, upstream mutation, or worktree
deletion occurred. The review base remained exact Stage 7
`c822897f38573d5fa3b09b19513dc169529854de`.

The project owner has confirmed authorization for Hermes reuse/adaptation. No Hermes
license name or terms are asserted here. Rollback keeps any desired explicit
private-store export outside Git, then disables/reverts the feedback UI/ranking
adapter; Pages, Actions cache, manifests, and Git history are not a feedback backup.

Task 2's historical behavior-level TDD evidence deviation remains explicitly
user-accepted and does not indicate missing current coverage. Local Task 6 validation,
finding-driven retest, and independent review are complete as recorded above.

## Stage 9 quality, cost and runtime notes (2026-07-26)

Stage 9A is complete locally through the committed baseline/profile decision.
RunManifest is schema `1.1` with pipeline identity `stage9-v1`; its strict metrics
result references the SHA-256 of a private, atomic `run-metrics.json` sidecar.
Sidecar failure is isolated from content status, viewer retention and Feishu status.
Workflow upload paths remain explicit: manifest plus metrics for the private run
artifact, viewer only for Pages.

The original synthetic fixture has exact 30/15/5 shape, CC0-1.0 SPDX provenance,
and no real identifier, title, author, abstract, URL, prompt or model response.
The reviewed nine-repetition steady-state report is
`docs/benchmarks/2026-07-26-stage9-baseline.{json,md}`. It observed median
`274,318,900 ns`, p95 `285,067,900 ns`, peak traced allocation `1,595,224` bytes,
zero network/paid calls, perfect golden quality ppm values and zero unsupported or
missing-field rates. All configured absolute and structural budgets passed.

The steady-state-only profile excluded startup, imports, warm-up, pytest, external
frames, fixture construction, benchmark code, inclusive wrappers and the artifact
audit security boundary. Two eligible project rows crossed the 20% gate. The
deterministic winner was `zotero_arxiv_daily.viewer.builder:build:19` with cumulative
`650,283,000 ns` of `2,443,789,600 ns` profiler time (`266,096 ppm`).
The exact Stage 9B plan is
`docs/superpowers/plans/2026-07-26-stage-9b-static-viewer-builder-parallel-writes.md`.

Post-review-repair verification observed focused Stage 9/daily/workflow tests
`180 passed, 2 skipped`; broad Stage 4–8 analysis/viewer/delivery/pipeline/workflow
regressions `533 passed`; and default pytest `784 passed, 2 failed, 2 skipped,
1 deselected`. The two failures are the unchanged Windows one-second multiprocessing
spawn baseline. The skips are platform-permission symlink tests. Compileall and
`git diff --check` passed; no Stage 9 test failed. Frozen sync, explicit slow suite
and final security/artifact audits are repeated after Stage 9B.

The first independent Stage 9A review found metrics isolation, timing boundary,
benchmark evidence, profile selection, pricing reachability and sidecar durability
gaps. Each confirmed finding was reproduced with focused failing tests and repaired
in commits `da8a913`, `638a559`, and `eb7bcad`. Two independent rereview passes then
found usage/retry attribution and benchmark metrics/identity gaps; focused RED
repairs now record retry at the real attempt boundary, execute the real
metrics/manifest path, include the Stage 4 fixture identity, and hash the complete
Python package allowlist. The final repair is committed before Stage 9B changes.

No real GitHub dispatch/Pages deployment, Zotero/private-library access, PDF/model
download, paid LLM call, Feishu send, push, PR, merge, upstream mutation or worktree
deletion occurred.

Stage 9B implemented one measured change only: independent viewer text files are
written through a prevalidated, bounded atomic batch with eight workers, while
`build-manifest.json` remains the final write. Sequential and parallel modes produce
byte-for-byte identical viewer directories, build counts and artifact hashes; a
worker failure cannot publish the manifest or trigger stale cleanup.

The reviewed comparison is
`docs/benchmarks/2026-07-26-stage9-parallel-viewer-writes.{json,md}`. A preliminary
15-pair CLI run improved median time by only `80,858 ppm`; this is the failed formal
Stage 9B verdict. A later 31-pair pass is retained only as exploratory non-verdict
evidence because its sample size was chosen after observing the failure.

Independent Stage 9C protocol commit `77fc731` then fixed exactly one 99-pair run
before new data existed and prohibited reruns or optional stopping. The confirmation
measured sequential median/p95 `234,164,600/259,528,100 ns` and parallel median/p95
`208,970,900/231,485,000 ns`. Median improvement was `107,590 ppm`; optimized p95
was `891,946 ppm` of reference. Peak traced allocation decreased from `2,060,272`
to `2,013,396` bytes. The single semantic equivalence hash was identical and the
ignored canonical result hash is tracked with the aggregate distribution.

The post-optimization offline audit retained exact 30/15/5 selection, five reviewed
viewer publications, all quality budgets, zero network calls and zero paid calls.
Profile schema v3 reported `no_eligible_target`: no remaining project row crossed
the unchanged 20% threshold. The Frontend Design, GSAP Core and GSAP Performance
boundary review found no visual or motion change; no GSAP dependency was introduced.
Final review repairs enforce stale-cleanup-before-manifest, preserve caller
tracemalloc state, reject root ancestor symlink/junction/reparse points and include
relative source paths in code identity.

Final verification after all review repairs observed:

- Frozen uv sync checked 172 packages.
- Stage 9/viewer/daily/workflow focused integration: `337 passed, 2 skipped`.
- Stage 4–8 broad regression group: `545 passed`.
- Default suite: `810 passed, 2 failed, 2 skipped, 1 deselected` in `59.05s`.
  The only failures are the unchanged Windows one-second multiprocessing spawn tests
  in `tests/retriever/test_arxiv_retriever.py`.
- Explicit `slow or not slow` execution reached its recorded 300-second process
  bound without completing and was terminated. No result is claimed; this is the
  known uncached local-reranker/Hugging Face environment limitation, and no test was
  removed, skipped or weakened.
- Workflow static safety: `14 passed`; compileall and branch-level
  `git diff --check` passed.
- Tracked scan covered 248 files with zero prohibited path, archive/cache/private,
  file-over-5-MiB or high-confidence secret hits. `.env` and `outputs/` remained
  ignored.
- Final fixture daily CLI returned success with one publication, zero deliveries,
  a six-file/32,606-byte viewer, matching manifest/artifact hash
  `b5a5d34221c814aa958de3a365464361a2e4da03825d07e3d8a914b91cc17efa`,
  matching metrics sidecar SHA-256, and zero forbidden viewer-content hits.

The independent final rereview found the initial 4 Important and 2 Minor issues
closed and reported no remaining findings. It independently verified protocol
ancestry, exactly one 99-pair result, 99+99 fresh measurement roots, two warmups,
raw result hash, recomputed distributions/gates, semantic equivalence, privacy,
compileall and branch diff hygiene.

Rollback of Stage 9B reverts commits `a3e6c51`, `3c1c257`, `c1fcb8c` and `c3ee32e`
in reverse order, restoring sequential writes without changing rendered content.
Rollback of Stage 9A removes the Stage 9 CI step and private sidecar upload, reverts manifest
identity to the prior version, and disables metrics construction without changing
Stages 1–8 content behavior. Raw ignored benchmark outputs may be removed after
retaining reviewed aggregate reports. Neither rollback may alter Stage 4 eligibility
or private feedback state.

## First controlled GitHub live dispatch (2026-07-28)

GitHub Actions run `30341813027` selected manual live/no-send on default branch and
passed checkout, frozen sync, safe-cache restore, delivery-ledger restore, and
Docling model preparation. The unified CLI then exited with code 2 while constructing
production dependencies, before a manifest, Pages artifact, Feishu request, or paid
LLM stage existed. The run log confirmed all live gates and required variable names
were present; the remaining eager constructor at that boundary was the uncached local
SentenceTransformer reranker.

The local repair pins `jinaai/jina-embeddings-v5-text-nano-retrieval` to commit
`ac5d898c8d382b17167c33e5c8af644a3519b47d`, includes that revision in embedding
cache identity, uses the explicit public cache directory `models/reranker`, and adds
a credential-free model preflight before the daily CLI. Workflow cache scope expands
only by that public model directory; analysis, validation, Zotero, feedback, outputs,
environment files and credentials remain excluded. Remote GitHub rerun and Pages
deployment are pending user push/dispatch. Rollback reverts this repair commit and
retains the last known-good Pages artifact with live/send gates disabled.

Local repair verification observed `185 passed` across the affected candidate,
document, daily, delivery and workflow suites. Default pytest reported `818 passed,
2 failed, 2 skipped, 1 deselected`; the only failures remain the two documented
Windows one-second multiprocessing spawn tests. Compileall, workflow static safety,
tracked path/archive/large-file checks and `git diff --check` passed. An actual
offline load of the pinned cached model reported dimension 768, revision-bearing
identity and successful close. Independent review reported no high-confidence
findings; the fresh GitHub Runner download remains the sole external validation.

Remote run `30343947289` then confirmed the repaired workflow reached the new model
preflight on commit `4493591`: Docling preparation succeeded, but the Jina load
failed after Hugging Face warned that the shared runner was making unauthenticated
Hub requests. The daily CLI, Zotero, LLM, Pages and Feishu boundaries were never
entered. The follow-up requires the read-only `HF_TOKEN` GitHub Secret before live
model preparation and adds fixed preflight error codes that never include the Hub
response body. Default dry-run remains independent of that token.

The follow-up local verification observed `186 passed` across the affected suites,
plus compileall and `git diff --check` success. Independent rereview confirmed that
`HF_TOKEN` is scoped only to the exact live-model gate, dry-run does not require it,
and neither the missing-token guard nor preflight classifier can print its value or
a dynamic Hub error.

Remote run `30346566106` on commit `fb74c5a` confirmed that `HF_TOKEN` was present
and Docling preparation succeeded, but the fixed Jina revision still failed with
the safe code `reranker_import_failed` while importing its custom model code on the
Linux runner. The daily CLI, Zotero, paid LLM, Pages and Feishu boundaries were not
entered. The Node.js 20 annotation came from the pinned v4 cache action and was not
the job failure.

The reviewed repair replaces that custom-code model with the public semantic-search
model `sentence-transformers/multi-qa-MiniLM-L6-cos-v1` at full revision
`b207367332321f8e44f96e224ef15bc607f4dbf0`, explicitly disables
`trust_remote_code`, and separates the retrieval identity from `encode()` kwargs.
Revision, task, encode settings and remote-code policy are all cache identity inputs.
Production construction, model preflight and the retained legacy local reranker use
the same revision/cache/policy values.

The offline comparison recorded at
`docs/benchmarks/2026-07-28-reranker-linux-repair.json` used only synthetic paper
topics and the already cached fixed revisions. Both models achieved 1,000,000 ppm
precision@5, recall@5 and NDCG@5. The observed local replacement snapshot was
91,579,532 bytes versus 4,556,721,955 bytes for the reference; this is a local cache
footprint, not a download-size guarantee. The user continued after the replacement
and its safety/quality trade-off were disclosed. The tracked strict-offline
recomputer is `tools/benchmarks/compare_rerankers.py`; it binds the complete
synthetic fixture hash, both model identities/encode settings, mean cosine
aggregation, stable input-order tie-breaking, metric calculation and verdict.

Final local repair verification observed:

- frozen uv 0.11.29 sync checked 172 packages;
- affected candidate/document/pipeline/delivery/workflow/benchmark tests:
  `306 passed, 1 deselected`;
- default suite: `824 passed, 2 failed, 2 skipped, 2 deselected` in `63.03s`;
- explicit offline `slow or not slow` suite:
  `826 passed, 2 failed, 2 skipped` in `78.56s`, including the real local-model
  encoding test and the actual two-model report recomputation;
- the only failures in both suites were the unchanged Windows one-second
  multiprocessing spawn tests in `tests/retriever/test_arxiv_retriever.py`;
- workflow static safety: `15 passed`; compileall and `git diff --check` passed;
- tracked/untracked deliverable scan covered 257 files with zero prohibited
  environment/cache/model/output path, archive, file-over-5-MiB or
  high-confidence secret hits;
- fixture daily dry-run succeeded with one publication, zero deliveries, six viewer
  files, matching sidecar/artifact hashes, and artifact hash
  `2f4889f313c4d02806eca457d7f3cb591a5362986953b21c67fcd05434937116`.

The two workflow cache steps now use the full-SHA Node.js 24 cache action v5.0.5, so
the unrelated Node.js 20 annotation is also removed. No real Zotero/private-library
read, paid LLM call, Feishu send, GitHub rerun, Pages deployment, push, PR, merge or
upstream mutation was performed. The remaining external acceptance is one user
push followed by one manual live/no-send dispatch. Rollback reverts the runner-safe
reranker repair, restores the prior Jina model identity and invalidates only the
model/embedding cache selected by the configuration hash; the live/send gates remain
disabled during rollback.

Independent review found one P2 in the first repair candidate: the comparison JSON
was privacy-tested but not reproducible from repository code. That finding was
reproduced with a failing import test and closed by the strict-offline recomputer,
pure scoring/tie-break tests and explicit slow fixed-revision recomputation above.
