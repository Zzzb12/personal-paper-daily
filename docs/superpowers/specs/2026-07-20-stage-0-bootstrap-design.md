# Stage 0 Repository Bootstrap Design

## Purpose

Bootstrap `personal-paper-daily` from `TideDra/zotero-arxiv-daily`, preserve a clean upstream relationship, establish the long-term product and architecture documentation, and record a reproducible baseline without implementing Stage 1 or later product features.

## Approved Scope

Stage 0 creates repository governance, documentation, safe configuration placeholders, ignore rules, and a factual baseline report. It may inspect and run the existing upstream project. It must not implement Zotero interest selection, candidate JSON generation, full-PDF analysis, Chinese LLM analysis, evidence extraction, Feishu delivery, a static viewer, feedback state, or a new scheduled pipeline.

No paid LLM call, real email delivery, bulk PDF download, or upstream write is allowed. Real credentials are not required for Stage 0 and must never be pasted into chat, committed, logged, or printed. A later stage may use credentials supplied through local environment variables or GitHub Secrets while checking only whether each variable is present.

## Repository Layout and Git Boundaries

- Repository root: `F:\Yan_0\Video_generaton\PaperDaily\personal-paper-daily`.
- Base project: `TideDra/zotero-arxiv-daily` at the cloned upstream commit.
- Upstream remote name: `upstream`.
- Stage 0 branch: `chore/bootstrap-personal-paper-daily`.
- Private GitHub repository name, when GitHub CLI authentication is available: `personal-paper-daily`.
- Private repository remote name: `origin`.
- Never force-push, create a public repository, create a pull request, modify upstream, add Hermes as a submodule, or merge Hermes Git history.
- The local Hermes archive remains outside the repository at `F:\Yan_0\Video_generaton\PaperDaily\hermes-arxiv-agent-main.zip` and must not be tracked.

The base repository is AGPL-3.0 and its license and notices remain intact. The user explicitly authorized inspection and modification of the local Hermes archive. The archive contains no `LICENSE`, `COPYING`, or `NOTICE`; its source and absent license are recorded. Stage 0 does not migrate Hermes business code. Later direct reuse must preserve provenance and be rechecked before any public distribution.

## Baseline Audit

The audit records the upstream commit, tag or absence of a tag, directory tree, configuration, entry point, Zotero client, paper retrievers, rerankers, PDF/LLM/email paths, tests, workflows, caching, logging, retries, and error handling.

Environment decisions follow repository evidence rather than assumptions:

- The cloned `pyproject.toml` declares Python `>=3.13`, `.python-version` contains `3.13`, and the lock/build workflow uses `uv`.
- The machine currently exposes Python 3.11.9 and pip 24.3.1; `uv`, Poetry, and PDM are absent, while the Windows `py` launcher points to an unavailable Python 3.12.
- Stage 0 must not alter global Git or Python configuration.
- If Python 3.13 and `uv` are required to reproduce the documented baseline, install or use them in a project-scoped way when practical; otherwise record the incompatibility and the exact blocked commands.
- Use only commands defined by the repository. The upstream instructions define `uv sync`, `uv run pytest`, and optional slow/coverage variants. No linter, formatter, or type checker is currently configured, so the baseline must report those checks as unavailable rather than invent commands.

Fast, slow, Docker-backed, network-dependent, credential-dependent, model-download, and potentially paid paths are classified separately. Stage 0 runs no paid or credential-dependent workflow. Any test failure triggers systematic debugging that preserves failing tests and assertions.

## Documentation Deliverables

- `AGENTS.md`: project goal, repository conventions, real commands, Superpowers workflow, TDD, evidence rules, Chinese output rules, structured schemas, secret handling, ignored private/generated data, network resilience, LLM caching, stage isolation, verification, and review requirements.
- `docs/PRODUCT_SPEC.md`: user scenario, inputs, recommendation and PDF-analysis flows, Chinese output contract, evidence binding, Feishu and web outputs, feedback states, non-goals, and first-version limits.
- `docs/ARCHITECTURE.md`: module inputs, outputs, dependencies, data flow, caches, error isolation, test seams, and reuse/adaptation/new-module decisions.
- `docs/IMPLEMENTATION_PLAN.md`: independently acceptable Stages 0–9, each with goal, non-goals, files, structures, implementation and test-first steps, acceptance criteria, risks, rollback, and proposed commits.
- `docs/BASELINE.md`: upstream revision, tree, reusable modules, commands and results, known issues, local operation, upstream updates, and Hermes provenance/license notes.
- `.env.example`: empty documented placeholders for Zotero, LLM, and Feishu settings.
- `.gitignore`: upstream rules plus `.env` exceptions, PDFs, parsed-paper and LLM caches, private Zotero data, local reader state, Python/test/IDE caches, logs, and temporary directories.

## Architectural Direction

The architecture retains narrow, testable boundaries. Existing retriever and reranker registries are preferred for reuse. The existing executor, Zotero access, configuration, protocols, PDF/LLM calls, and email path are classified after source inspection as direct reuse, adaptation, or replacement; no core interface is removed or changed without approval.

New design boundaries cover the Zotero interest provider, candidate store, PDF downloader and parser, section parser, caption detector, evidence extractor, document mapper, structured analysis schemas, LLM analyzer, evidence validator, Feishu renderer/client, static viewer builder, feedback store, and GitHub Actions pipeline.

The long-term data flow is:

1. Select Zotero interest papers from approved collection paths while excluding `PaperDaily/99-Exclude/**`.
2. Retrieve arXiv candidates from `cs.CV`, `cs.LG`, and `cs.AI`.
3. Rank a pool of 30 with embeddings, optionally LLM-rerank at most 15, and fully analyze at most 5.
4. Download and parse full PDFs only for the highest-ranked papers.
5. Produce Chinese analysis in the fixed Insight → evidence → Method → parameters → ablations → conclusions → limitations order.
6. Validate every insight, parameter, and ablation against page/section/Figure-or-Table evidence.
7. Render at most five detailed Feishu items while retaining lower-priority papers on the static site.
8. Persist read, favorite, and irrelevant feedback outside tracked local state.

Every evidence item records PDF page, section, Figure/Table label, caption, evidence text, image path, confidence, and how the visual supports the claim. Missing information is `null` or explicitly “论文未明确提供”. Outputs distinguish author statements, system summaries, and system inference; inference always has `inferred=true`.

## Hermes Reference Boundary

Stage 0 may extract the user-provided archive into an isolated reference location and inspect its Feishu rendering, Chinese generation prompts/data, static viewer, JSON files, and browser-local feedback. It does not add the archive or extracted datasets to Git and does not copy business code into the product in Stage 0.

Later stages may adapt or reimplement the useful concepts, including compact Chinese cards, date/search views, a generated static data bundle, GitHub Pages publishing, and `localStorage` feedback. Any direct code migration is isolated in a reviewable commit with provenance noted and is prohibited from silently replacing upstream core interfaces.

## Verification and Git Completion

Before Stage 0 is declared complete:

1. Confirm branch, upstream, and origin state.
2. Record dependency installation and every configured baseline command with exact results.
3. Validate all required document sections and placeholders.
4. Test ignore rules using representative non-secret dummy paths.
5. Scan tracked content and the pending diff for credential patterns, PDFs, Zotero private data, caches, and unexpected large files without printing secret values.
6. Confirm that no Stage 1+ implementation is present.
7. Use `verification-before-completion` and then `requesting-code-review`; fix confirmed findings and rerun affected verification.
8. Create `chore: bootstrap personal paper daily project` only if Git identity is already configured. Never configure an unknown identity.
9. Push the original default branch and current branch only when an authenticated private `origin` exists. Do not create a pull request.

If GitHub CLI remains unauthenticated, local work continues without a fabricated origin. The final report gives only the minimum login and private-repository commands the user must run.

## Acceptance Boundary

Stage 0 is complete only when every requested document and safety rule is present, the upstream relationship and development branch are verifiable, baseline results are truthful, the diff is reviewed, the repository contains no secrets/PDFs/private Zotero data, and commit/push outcomes or blockers are explicitly reported. It is not complete merely because documentation files exist.
