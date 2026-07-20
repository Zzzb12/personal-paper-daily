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
