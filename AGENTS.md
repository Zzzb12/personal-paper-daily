# Personal Paper Daily Agent Guide

## Project goal

`personal-paper-daily` recommends new arXiv papers from a user's Zotero research interests, fully analyzes only the highest-ranked PDFs, produces evidence-bound Chinese readings, and publishes concise Feishu messages plus a responsive static reader. Every conclusion must remain traceable to the paper; missing evidence is preferable to invented evidence.

The roadmap is implemented one stage at a time. Stage 0 contains repository initialization, baseline verification, governance, product specification, architecture, and plans only. Do not begin a later stage unless the user explicitly requests that stage.

## Repository conventions

- `src/zotero_arxiv_daily/`: upstream Python package and future product modules.
- `tests/`: tests mirroring the Python package; every behavior change starts here.
- `config/`: Hydra configuration without real credentials or private library data.
- `docs/`: product, architecture, roadmap, and factual baseline documents.
- `docs/superpowers/specs/`: approved design specifications.
- `docs/superpowers/plans/`: executable Superpowers plans.
- Future generated/private data belongs under ignored `data/`, `cache/`, or local viewer-state paths, never in source directories.

Do not rename, remove, or change an existing core interface without explicit user confirmation. Prefer adapters around the current `Executor`, `Paper`, `CorpusPaper`, retriever registry, and reranker registry.

## Setup and commands

The upstream project requires Python 3.13 and uses uv with a committed lock file.

```powershell
uv sync --frozen
uv run src/zotero_arxiv_daily/main.py
uv run pytest
uv run pytest -m "" --cov=src/zotero_arxiv_daily --cov-report=term-missing
```

`uv run pytest` excludes tests marked `slow`. The complete CI command includes a slow embedding-model test that accesses Hugging Face. No lint, formatter, or static type-check command is configured at the Stage 0 baseline; report that fact instead of inventing a command.

On Windows, two upstream hard-timeout tests are known to fail because the one-second assertion includes `multiprocessing` `spawn` startup and heavy module imports. Do not skip or weaken them. See `docs/BASELINE.md` for evidence.

## Required Superpowers workflow

1. Start by invoking `using-superpowers` and identify every applicable skill.
2. Use `brainstorming` before designing or changing behavior.
3. Use `writing-plans` before multi-step implementation.
4. Use `using-git-worktrees` or verify that the requested isolated branch/workspace already exists.
5. Use `test-driven-development` before every feature, bug fix, refactor, or behavior change.
6. Use `systematic-debugging` before proposing a fix for any failure or unexpected behavior.
7. Use `verification-before-completion` before claiming a stage is complete.
8. Use `requesting-code-review` before the final stage commit.
9. Use `finishing-a-development-branch` for branch handoff.

If a skill imposes a user-review gate, stop at that gate. Do not claim that a skill was used unless its current `SKILL.md` was read and followed.

## Test-first development

- Every new feature begins with a focused failing test that demonstrates the desired behavior.
- Confirm the test fails for the intended reason before writing implementation code.
- Implement only enough behavior to pass, then run the focused test and the relevant regression suite.
- Never delete, skip, relax, or rewrite a test merely to make a build green.
- Network integrations require unit tests with fakes or recorded fixtures and separate opt-in integration tests.
- Paid LLM paths require deterministic fake-client contract tests. Unit tests must never call a paid API.
- Schema migrations require backward-compatibility and invalid-payload tests.
- Renderer tests should assert semantic fields and safe links, not brittle full-page snapshots alone.

## Evidence and anti-hallucination rules

Never fabricate a paper claim, parameter, Figure, Table, caption, experiment, setting, numerical result, code link, or limitation.

- An Insight cannot be inferred from the Abstract alone.
- Prefer Introduction, Motivation, Observation, Analysis, Method, Experiments, Ablation Study, and Appendix.
- Every key Insight must bind to at least one evidence record.
- Every ablation conclusion must bind to the corresponding Figure or Table.
- Supporting visuals must explain how the Figure/Table supports the Insight; a label alone is insufficient.
- If a required fact cannot be found, store `null` or display “论文未明确提供”.
- Distinguish `author_statement`, `system_summary`, and `system_inference`.
- Every system inference must set `inferred=true`; author statements and summaries set `inferred=false`.
- Confidence is an assessment of extraction/binding quality, not permission to invent a claim.

Each evidence record must preserve:

- PDF page number in the original PDF coordinate system.
- Section title/path.
- Figure/Table label, or `null` when the evidence is text-only.
- Caption, or `null` when unavailable.
- Verbatim-bounded evidence text.
- Extracted image path, or `null` for text-only evidence.
- Confidence.
- Claim source type and `inferred` flag.
- A short explanation of how the evidence supports the claim.

Do not silently convert parser page indices to display page numbers. Record both if they differ and define the mapping.

## Chinese output contract

User-facing analysis is written in concise, technically precise Chinese while preserving the English paper title, symbols, model names, dataset names, and metric names. Each fully analyzed paper follows this order:

1. Insight.
2. Supporting Figure/Table evidence.
3. Method.
4. Key parameters used by the Insight and Method.
5. Parameter-related ablations.
6. Experimental conclusions.
7. Limitations.

Also include the Chinese title, recommendation reason, research problem, Insight formation logic, module roles, differences from prior work, parameter name/symbol/function/value, how each parameter was selected, whether per-model tuning is required, and PDF/arXiv/code links.

## Structured schemas

All inter-module payloads must use Pydantic models or an equivalently strict validated schema. Do not pass undocumented free-form dictionaries across module boundaries.

Schemas must:

- Reject unknown or malformed required fields where silent acceptance could lose evidence.
- Use explicit optional fields instead of sentinel strings internally.
- Include a `schema_version` on persisted records.
- Preserve stable paper IDs and evidence IDs.
- Serialize dates, paths, enum values, confidence, and inference flags deterministically.
- Validate that evidence references resolve to known pages/sections/visuals.
- Separate raw source text from system-generated analysis.

## Secrets and private data

- Read keys only from environment variables or GitHub Secrets.
- Never commit `.env` or `.env.*`; `.env.example` is the only tracked exception and contains empty values.
- Never ask the user to paste real credentials into chat.
- Never print, log, snapshot, or include secret values in exception messages.
- Never read the clipboard to look for credentials.
- Never commit Zotero private exports, collection contents, item notes, or personal library metadata.
- Never commit downloaded paper PDFs.
- Never commit parsed-paper caches, LLM response caches, run caches, local reader state, logs, or temporary output.
- Secret scans should report filenames and rule names, not the matched secret value.

## Network, reliability, and cost

- Every HTTP request requires an explicit connect/read timeout.
- Retry only transient failures with a bounded attempt count and backoff; respect rate limits and `Retry-After` where available.
- Do not retry authentication, validation, or permanent 4xx failures except 408/409/425/429 when appropriate.
- Isolate failure per paper so one malformed PDF or unavailable service does not discard the entire daily run.
- Persist enough non-sensitive state for idempotent reruns.
- Cache embedding, parsed-document, and expensive LLM results by content/model/prompt/schema version.
- Never reuse a cache entry when its model, prompt, parser, input hash, or schema version differs.
- Put the ranking and cheap validation boundary before PDF download and LLM analysis.
- Record estimated and actual expensive-call counts without logging prompts that may contain private Zotero content.

## Stage discipline and completion

- Implement only the currently approved roadmap stage.
- Do not fold opportunistic refactors or later-stage features into the current diff.
- Preserve upstream behavior unless the stage explicitly changes it.
- Before completion, run the configured tests and all stage-specific validators from a cleanly understood working tree.
- Inspect the full diff, run a secret/large-file/private-data scan, and confirm ignored paths behave as intended.
- Request an independent code review and resolve every confirmed finding.
- Report failures, skipped external operations, missing credentials, network dependencies, and cost-bearing steps honestly.
- A documentation-only stage is not complete until its factual claims match command output and source inspection.
