# Stage 0 Repository Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a safe, documented, reproducible Stage 0 baseline for `personal-paper-daily` without implementing any Stage 1 or later business feature.

**Architecture:** Keep the cloned AGPL upstream intact and add governance, product, architecture, roadmap, environment-template, ignore-rule, and baseline artifacts around it. Inspect the user-licensed Hermes source outside the repository, classify both codebases, and verify the resulting branch through deterministic document, Git, ignore-rule, secret, large-file, and upstream-test checks.

**Tech Stack:** Python 3.13, uv, pytest, Hydra/OmegaConf, Git, GitHub CLI, PowerShell 7, Markdown, YAML.

## Global Constraints

- Repository root is `F:\Yan_0\Video_generaton\PaperDaily\personal-paper-daily`.
- Branch is `chore/bootstrap-personal-paper-daily`; `upstream` is read-only and no force push is allowed.
- The user-supplied Hermes archive is licensed for use, modification, adaptation, and reuse; keep its provenance.
- Do not implement Stage 1–9 behavior in Stage 0.
- Do not call a paid LLM, send email, use real Zotero credentials, download papers, or print environment-variable values.
- Do not ask the user to paste credentials into chat. Future credentials belong in local environment variables or GitHub Secrets.
- Do not modify global Git identity or global Python configuration.
- Do not delete, skip, weaken, or alter upstream tests to obtain a passing result.
- Preserve every existing `.gitignore` rule.
- Do not commit `.env`, PDFs, private Zotero data, parsed-paper/LLM caches, local reader state, logs, temporary files, or the Hermes archive/extraction.
- The upstream repository contains no configured lint, formatter, or static type-check command; report those checks as not configured.
- The already-approved design commits remain separate. All remaining Stage 0 deliverables use the required final commit `chore: bootstrap personal paper daily project` after review and verification.

---

### Task 1: Confirm Git and worktree safety

**Files:**
- Read: `docs/superpowers/specs/2026-07-20-stage-0-bootstrap-design.md`
- Read: `.git/config`
- No file changes

**Interfaces:**
- Consumes: approved Stage 0 design, existing clone, `upstream` remote
- Produces: verified primary-worktree state and immutable baseline identifiers for later documentation

- [ ] **Step 1: Invoke the worktree skill**

Read and follow `superpowers:using-git-worktrees`. Because the clone and required branch already exist in the requested repository root, use its environment-detection step to decide whether an additional worktree is justified; do not create a nested worktree.

- [ ] **Step 2: Verify worktree identity**

Run:

```powershell
git rev-parse --git-dir
git rev-parse --git-common-dir
git branch --show-current
```

Expected: both Git directory commands identify the same primary `.git` directory and the branch is `chore/bootstrap-personal-paper-daily`.

- [ ] **Step 3: Verify remotes and upstream revision**

Run:

```powershell
git remote -v
git rev-parse HEAD
git rev-parse upstream/main
git describe --tags --always upstream/main
git status --short --branch
```

Expected: `upstream` points to `https://github.com/TideDra/zotero-arxiv-daily.git`; no `origin` is fabricated; status contains only the plan file at this point.

- [ ] **Step 4: Verify the licensed archive remains outside Git scope**

Run:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'F:\Yan_0\Video_generaton\PaperDaily\hermes-arxiv-agent-main.zip'
git ls-files | Select-String -Pattern 'hermes-arxiv-agent|\.zip$'
```

Expected: archive hash is `5456921C484DF0F6BE35DB07A60CA7D9B25E40A913EDDC5BD9269D830AAB42A6`; the tracked-file search returns no matches.

---

### Task 2: Add secret-safe configuration and ignore rules test-first

**Files:**
- Create: `.env.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: upstream ignore rules
- Produces: documented environment-variable names and deterministic protection for private/generated paths

- [ ] **Step 1: Invoke the TDD skill**

Read and follow `superpowers:test-driven-development`. Treat `git check-ignore` assertions as the tests for this configuration behavior.

- [ ] **Step 2: Run the pre-change ignore test and confirm it fails**

Run:

```powershell
$paths = @(
  '.env.local',
  'data/papers/sample.pdf',
  'cache/papers/sample.json',
  'cache/llm/response.json',
  'data/zotero/library.json',
  'viewer/.reader-state.json',
  '.pytest_cache/CACHEDIR.TAG',
  '.idea/workspace.xml',
  'logs/stage0.log',
  'tmp/work.txt',
  '.bootstrap-tools/pyvenv.cfg'
)
$missed = foreach ($path in $paths) {
  git check-ignore --quiet --no-index -- $path
  if ($LASTEXITCODE -ne 0) { $path }
}
if ($missed.Count -eq 0) { throw 'Expected at least one pre-change ignore assertion to fail.' }
$missed
```

Expected: the command lists multiple paths not protected by the upstream rules.

- [ ] **Step 3: Extend `.gitignore` without removing upstream rules**

Append categorized rules for:

```gitignore
# Local secrets (keep the documented template)
.env.*
!.env.example

# Project-local bootstrap tooling and environments
.bootstrap-tools/

# Downloaded papers and private Zotero exports
data/papers/
data/zotero/
*.pdf

# Generated paper and LLM caches
cache/
data/cache/

# Local static-viewer feedback state
viewer/.reader-state.json
viewer/favorites.json
viewer/feedback.json
viewer/**/.reader-state.json
viewer/**/reader-state.json
viewer/**/favorites.json
viewer/**/feedback.json

# Python and test caches
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/
.ruff_cache/

# IDE-local configuration
.idea/

# Logs and temporary directories
*.log
tmp/
temp/
```

- [ ] **Step 4: Create the empty environment template**

Create `.env.example` with comments explaining purpose and these empty assignments only:

```dotenv
# Zotero read-only API credentials. Set locally; never commit real values.
ZOTERO_ID=
ZOTERO_KEY=

# OpenAI-compatible LLM endpoint. Stage 0 does not call it.
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=

# Feishu application and target chat. Stage 0 does not call Feishu.
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_CHAT_ID=
```

- [ ] **Step 5: Run the post-change ignore tests**

Run the Step 2 loop with this final assertion:

```powershell
if ($missed.Count -ne 0) { throw "Unignored sensitive/generated paths: $($missed -join ', ')" }
git check-ignore --quiet --no-index -- .env.example
if ($LASTEXITCODE -eq 0) { throw '.env.example must remain trackable.' }
```

Expected: no missed paths and `.env.example` remains trackable.

- [ ] **Step 6: Do not commit yet**

Keep the reviewed Stage 0 deliverables together for the user-required final commit.

---

### Task 3: Audit upstream and the licensed Hermes source

**Files:**
- Read: `README.md`
- Read: `LICENSE`
- Read: `pyproject.toml`
- Read: `uv.lock`
- Read: `config/*.yaml`
- Read: `src/zotero_arxiv_daily/**/*.py`
- Read: `tests/**/*.py`
- Read: `.github/workflows/*.yml`
- Read outside repo: `F:\Yan_0\Video_generaton\PaperDaily\hermes-reference\hermes-arxiv-agent-main/**`
- No product-code changes

**Interfaces:**
- Consumes: upstream clone and licensed Hermes ZIP
- Produces: source-grounded reuse/adapt/new classifications, network/cost map, workflow map, and provenance notes

- [ ] **Step 1: Extract Hermes outside the repository without overwriting**

Run:

```powershell
$referenceRoot = 'F:\Yan_0\Video_generaton\PaperDaily\hermes-reference'
$archive = 'F:\Yan_0\Video_generaton\PaperDaily\hermes-arxiv-agent-main.zip'
if (-not (Test-Path -LiteralPath $referenceRoot)) {
  New-Item -ItemType Directory -Path $referenceRoot | Out-Null
  Expand-Archive -LiteralPath $archive -DestinationPath $referenceRoot
}
Get-ChildItem -Recurse -Force -LiteralPath $referenceRoot | Select-Object FullName,Length
```

Expected: extraction exists only under `hermes-reference`; no existing directory is removed or overwritten.

- [ ] **Step 2: Inspect every upstream responsibility named in the product request**

Read the complete relevant files and record exact classes/functions for the entry point, `Executor`, Zotero calls, retrievers, rerankers, PDF/text helpers, LLM methods, email renderer/sender, tests, workflows, logs, retry/timeout logic, and cache behavior.

Run targeted symbol discovery with PowerShell because bundled `rg.exe` is unavailable in this environment:

```powershell
Get-ChildItem -Recurse -File src,tests,.github,config |
  Select-String -Pattern 'Zotero|arxiv|embedding|rerank|pdf|OpenAI|smtp|cache|retry|timeout|logger|loguru'
```

Expected: each requested responsibility has a file/symbol citation or an explicit finding that the mechanism is absent.

- [ ] **Step 3: Inspect the licensed Hermes implementation**

Read `monitor.py`, `reextract_affiliations.py`, `AGENT_SKILL.md`, the cron prompts, `feishu_msg.md`, `feishu_output.json`, `llm_results.json`, `merged_llm_results.json`, `.github/workflows/pages.yml`, and every file under `viewer/` and `scripts/`.

Record:

- Feishu payload composition and send path.
- Chinese generation prompt and stored fields.
- Static viewer build and serving flow.
- JSON shapes and generated/hand-maintained boundaries.
- Favorite/read behavior and `localStorage` keys.
- Reusable implementation versus data samples that must not enter Git.
- Archive SHA-256 and the project owner's licensed-source authorization.

- [ ] **Step 4: Classify the two codebases**

Use these categories consistently in the architecture and baseline documents:

- Direct reuse: stable existing interface and behavior satisfies the planned responsibility.
- Adapt: retain the interface or implementation core but add a narrow wrapper/configuration.
- New: no suitable implementation exists.
- Licensed Hermes reuse: migrate or modify suitable Hermes implementation in the later owning stage with provenance.

- [ ] **Step 5: Do not modify application code**

Confirm:

```powershell
git diff --name-only -- src config .github/workflows
```

Expected: no output.

---

### Task 4: Reproduce the upstream Python baseline

**Files:**
- Generated outside the repository: `F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\`
- Generated and ignored by upstream: `.venv/`
- Modify later with results: `docs/BASELINE.md`

**Interfaces:**
- Consumes: `.python-version`, `pyproject.toml`, `uv.lock`, upstream test suite
- Produces: exact dependency/test results and classified failures without credential or paid-service use

- [ ] **Step 1: Confirm the environment mismatch**

Run:

```powershell
python --version
uv --version
Get-Content -Raw .python-version
Select-String -Path pyproject.toml -Pattern 'requires-python'
```

Expected before setup: Python 3.11.9; global `uv` unavailable; project requires Python 3.13.

- [ ] **Step 2: Install uv into a repository-external tools environment**

Run:

```powershell
python -m venv F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\python.exe -m pip install --upgrade pip uv
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe --version
```

Expected: a uv version is printed; no repository file, PATH entry, or global Python configuration changes.

- [ ] **Step 3: Provision the declared Python and sync the frozen lock**

Run:

```powershell
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe python install 3.13
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe sync --frozen
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run python --version
```

Expected: `uv run python --version` reports Python 3.13.x and dependency sync completes from `uv.lock`.

- [ ] **Step 4: Collect and run the default test suite**

Run:

```powershell
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run pytest --collect-only -q
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run pytest
```

Expected: collection count is recorded; default tests exclude `slow` through `pyproject.toml`.

- [ ] **Step 5: Identify and run the upstream CI suite**

Run:

```powershell
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run pytest -m slow --collect-only -q
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run pytest -m "" --cov=src/zotero_arxiv_daily --cov-report=term-missing
```

Expected: slow-test count and the complete CI result are recorded. This command may download the configured embedding model and access public model infrastructure, but it must not use credentials or a paid API.

- [ ] **Step 6: Apply systematic debugging to every failure**

If installation or a test command fails, invoke and follow `superpowers:systematic-debugging`. Reproduce the smallest failing command, identify whether the root cause is source, dependency, Windows multiprocessing/path behavior, network/model access, or environment, and record evidence. Do not change tests or business code during Stage 0.

- [ ] **Step 7: Record unavailable quality commands**

Verify no Ruff, Flake8, Black, Mypy, Pyright, or basedpyright configuration/dependency exists:

```powershell
Select-String -Path pyproject.toml -Pattern '\[tool\.(ruff|black|mypy|pyright|basedpyright|flake8)'
Select-String -Path pyproject.toml -Pattern 'ruff|flake8|black|mypy|pyright|basedpyright'
```

Expected: no matches. Report lint and type checking as not configured; do not substitute unrelated commands.

---

### Task 5: Write governance and product specification

**Files:**
- Create: `AGENTS.md`
- Create: `docs/PRODUCT_SPEC.md`

**Interfaces:**
- Consumes: approved design and all fixed product requirements
- Produces: repository-wide agent rules and authoritative product contract

- [ ] **Step 1: Write document-content assertions before the documents**

Run and confirm failure because the files do not yet exist:

```powershell
Test-Path AGENTS.md
Test-Path docs\PRODUCT_SPEC.md
```

Expected: both are `False`.

- [ ] **Step 2: Create `AGENTS.md`**

Include explicit sections for project goal, directory conventions, the commands verified in Task 4, Superpowers order, test-first development, evidence non-fabrication, page/Figure/Table binding, Chinese output, Pydantic-or-equivalent schemas, environment-only secrets, forbidden tracked data, timeout/retry policy, expensive-call caching, one-roadmap-stage-at-a-time execution, verification, review, and core-interface change approval.

- [ ] **Step 3: Create `docs/PRODUCT_SPEC.md`**

Encode every approved Zotero path, topic, category, numeric limit, Chinese field, evidence rule, Feishu/web/feedback behavior, non-goal, and v1 constraint. Mark Stage 0 as documentation/baseline only and preserve `null`, “论文未明确提供”, author-statement/system-summary/inference distinctions, and `inferred=true`.

- [ ] **Step 4: Validate required content**

Run:

```powershell
$agentsTerms = @('Superpowers','test','Figure','Table','Pydantic','.env','timeout','retry','cache','review','core interface')
$productTerms = @('PaperDaily/00-Seeds/**','PaperDaily/99-Exclude/**','candidate_pool_size = 30','full_analysis_limit = 5','inferred=true','论文未明确提供','Feishu','static')
foreach ($term in $agentsTerms) { if (-not (Select-String -Quiet -Path AGENTS.md -SimpleMatch $term)) { throw "AGENTS.md missing: $term" } }
foreach ($term in $productTerms) { if (-not (Select-String -Quiet -Path docs\PRODUCT_SPEC.md -SimpleMatch $term)) { throw "PRODUCT_SPEC.md missing: $term" } }
```

Expected: no exception.

---

### Task 6: Write architecture with testable module contracts

**Files:**
- Create: `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: upstream/Hermes audit and product specification
- Produces: module contracts and the authoritative reuse/adapt/new map

- [ ] **Step 1: Confirm the architecture document is absent**

Run `Test-Path docs\ARCHITECTURE.md`.

Expected: `False`.

- [ ] **Step 2: Write the architecture document**

For all 18 required modules—Zotero interest provider, arXiv retriever, embedding reranker, candidate store, PDF downloader, PDF parser, section parser, caption detector, evidence extractor, document mapper, LLM analyzer, evidence validator, structured schemas, Feishu renderer, Feishu client, static viewer builder, feedback store, and GitHub Actions pipeline—define:

- Responsibility and owning stage.
- Typed/structured input and output.
- Dependencies and forbidden dependencies.
- Cache boundary and stable cache key.
- Timeout/retry/error-isolation behavior.
- Test seam and mock/fake strategy.
- Reuse, adapt, new, or licensed-Hermes-reuse decision with source file evidence.

Include the end-to-end data flow, failure containment, cost boundary before full PDF/LLM work, evidence lineage, and local-versus-tracked state model.

- [ ] **Step 3: Validate every required module name**

Run:

```powershell
$modules = @('Zotero interest provider','arXiv retriever','embedding reranker','candidate store','PDF downloader','PDF parser','section parser','caption detector','evidence extractor','document mapper','LLM analyzer','evidence validator','structured schemas','Feishu renderer','Feishu client','static viewer builder','feedback store','GitHub Actions pipeline')
foreach ($module in $modules) { if (-not (Select-String -Quiet -Path docs\ARCHITECTURE.md -SimpleMatch $module)) { throw "ARCHITECTURE.md missing: $module" } }
```

Expected: no exception.

---

### Task 7: Write the long-term Stage 0–9 roadmap

**Files:**
- Create: `docs/IMPLEMENTATION_PLAN.md`

**Interfaces:**
- Consumes: product and architecture documents
- Produces: independently acceptable roadmap stages without implementing them

- [ ] **Step 1: Confirm the roadmap is absent**

Run `Test-Path docs\IMPLEMENTATION_PLAN.md`.

Expected: `False`.

- [ ] **Step 2: Write all ten roadmap stages**

For Stage 0 through Stage 9, include these exact subsections:

```markdown
### Goal
### Non-goals
### Files
### Data structures
### Test-first implementation steps
### Acceptance criteria
### Risks
### Rollback
### Suggested commits
```

Use the approved stage names and place each architecture module in exactly one primary owning stage. Provide exact proposed paths and schema/interface names for future work, but do not create those application files in Stage 0.

- [ ] **Step 3: Validate roadmap coverage**

Run:

```powershell
0..9 | ForEach-Object {
  if (-not (Select-String -Quiet -Path docs\IMPLEMENTATION_PLAN.md -SimpleMatch "Stage $_")) {
    throw "IMPLEMENTATION_PLAN.md missing Stage $_"
  }
}
$required = @('Goal','Non-goals','Files','Data structures','Test-first implementation steps','Acceptance criteria','Risks','Rollback','Suggested commits')
foreach ($heading in $required) {
  $count = (Select-String -Path docs\IMPLEMENTATION_PLAN.md -SimpleMatch "### $heading").Count
  if ($count -ne 10) { throw "Expected 10 '$heading' headings, found $count" }
}
```

Expected: all stages and exactly ten copies of each required subsection.

---

### Task 8: Write the factual baseline report

**Files:**
- Create: `docs/BASELINE.md`

**Interfaces:**
- Consumes: Tasks 1, 3, and 4 evidence
- Produces: reproducible baseline, exact failures, and upstream-update instructions

- [ ] **Step 1: Create `docs/BASELINE.md` from captured facts**

Include upstream URL, commit/tag, branch, directory summary, Python/uv/package-manager versions, install command/result, collected/default/full test counts and results, coverage result, lint/type-check status, network/model/API classification, external-key requirements, cost risks, logs/errors/caches, reusable/adapt/new modules, local run command, and upstream fetch/rebase-or-merge guidance that never pushes to upstream.

Include a licensed-source provenance record for the Hermes ZIP with its path, SHA-256, extraction location, inspected functions/data formats, and reusable components. Do not claim that sample JSON is safe to commit unless inspection proves it contains no personal or generated data.

- [ ] **Step 2: Validate factual placeholders and required facts**

Run:

```powershell
$unresolvedMarkers = @(('T' + 'BD'), ('TO' + 'DO'), 'unknown result', 'fill in')
foreach ($marker in $unresolvedMarkers) {
  if (Select-String -Quiet -Path docs\BASELINE.md -SimpleMatch $marker) { throw "BASELINE.md contains unresolved marker: $marker" }
}
$terms = @('05b20ec','Python','uv','pytest','lint','type','network','API','upstream','AGPL','Hermes','5456921C484DF0F6BE35DB07A60CA7D9B25E40A913EDDC5BD9269D830AAB42A6')
foreach ($term in $terms) { if (-not (Select-String -Quiet -Path docs\BASELINE.md -SimpleMatch $term)) { throw "BASELINE.md missing: $term" } }
```

Expected: no placeholders and all facts are present. If upstream HEAD changed after this plan was written, replace `05b20ec` in both the report and validation command with the actually cloned commit.

---

### Task 9: Verify, review, and finish the branch

**Files:**
- Review: `.gitignore`
- Review: `.env.example`
- Review: `assets/use_docker.md`
- Review: `AGENTS.md`
- Review: `docs/PRODUCT_SPEC.md`
- Review: `docs/ARCHITECTURE.md`
- Review: `docs/IMPLEMENTATION_PLAN.md`
- Review: `docs/BASELINE.md`
- Review: `docs/superpowers/plans/2026-07-20-stage-0-bootstrap.md`
- No Stage 1+ files

**Interfaces:**
- Consumes: complete Stage 0 diff and recorded baseline
- Produces: reviewed final commit and optional push only to authenticated private `origin`

- [ ] **Step 1: Invoke completion verification**

Read and follow `superpowers:verification-before-completion`. Rerun the ignore assertions, document assertions, default tests, `git diff --check`, and every check affected by any late fix.

- [ ] **Step 2: Inspect the complete diff and Stage 0 scope**

Run:

```powershell
git status --short
git diff --stat HEAD
git diff --check HEAD
git diff --name-only HEAD
git diff HEAD -- . ':!uv.lock'
```

Expected application-code check:

```powershell
$unexpected = git diff --name-only HEAD -- src config .github/workflows tests
if ($unexpected) { throw "Stage 0 modified application/CI/test files: $($unexpected -join ', ')" }
```

- [ ] **Step 3: Scan tracked and pending content for forbidden artifacts**

Run filename/type checks:

```powershell
$trackedForbidden = git ls-files | Where-Object {
  $_ -ne '.env.example' -and
  $_ -match '(^|/)(\.env($|\.)|cache/|data/papers/|data/zotero/)|\.pdf$|reader-state|favorites\.json|feedback\.json|hermes-arxiv-agent|\.zip$'
}
if ($trackedForbidden) { throw "Forbidden tracked files: $($trackedForbidden -join ', ')" }
$large = git ls-files | ForEach-Object { Get-Item -LiteralPath $_ } | Where-Object Length -gt 5MB
if ($large) { throw "Unexpected tracked files over 5 MiB: $($large.FullName -join ', ')" }
```

Scan text without printing matched values:

```powershell
$patterns = @(
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----',
  '(?i)(api[_-]?key|app[_-]?secret|zotero[_-]?key|sender[_-]?password)\s*[:=]\s*["'']?[A-Za-z0-9_-]{16,}["'']?',
  'sk-[A-Za-z0-9_-]{20,}'
)
$files = git ls-files | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
$hitFiles = foreach ($file in $files) {
  foreach ($pattern in $patterns) {
    if (Select-String -Quiet -LiteralPath $file -Pattern $pattern) { $file; break }
  }
}
if ($hitFiles) { throw "Potential secret patterns found in: $($hitFiles -join ', ')" }
```

Expected: no forbidden files, unexpected large files, or secret-pattern files.

- [ ] **Step 4: Request independent code review**

Read and follow `superpowers:requesting-code-review`. Dispatch an independent reviewer to compare the full diff with the approved design, the user's Stage 0 acceptance criteria, security requirements, and actual command evidence. Ask the reviewer to rank findings by severity and cite exact file/line locations.

- [ ] **Step 5: Resolve confirmed review findings**

For each finding, verify it against the repository and requirements. Apply only confirmed fixes, rerun the affected deterministic checks, and record any rejected finding with evidence. Do not add Stage 1 behavior.

- [ ] **Step 6: Recheck GitHub authentication and origin**

Run:

```powershell
gh auth status
git remote -v
```

If unauthenticated, leave `origin` absent and document these minimum user commands without executing them:

```powershell
gh auth login
gh repo create personal-paper-daily --private --source . --remote origin
git push origin upstream/main:main
git push -u origin chore/bootstrap-personal-paper-daily
```

If authenticated, run this guarded sequence. An existing repository is not overwritten or repointed automatically:

```powershell
$githubUser = gh api user --jq .login
$repoName = "$githubUser/personal-paper-daily"
gh repo view $repoName --json name,visibility
if ($LASTEXITCODE -eq 0) { throw "$repoName already exists; stop for explicit user direction." }
gh repo create personal-paper-daily --private --source . --remote origin
gh repo view $repoName --json visibility --jq .visibility
git push origin upstream/main:main
git push -u origin chore/bootstrap-personal-paper-daily
```

Expected: repository visibility is `PRIVATE`, both branches push to `origin`, and no pull request is created.

- [ ] **Step 7: Create the required final Stage 0 commit**

Confirm identity exists without changing it:

```powershell
git config --get user.name
git config --get user.email
```

Then run:

```powershell
git add -- .gitignore .env.example assets/use_docker.md AGENTS.md docs/PRODUCT_SPEC.md docs/ARCHITECTURE.md docs/IMPLEMENTATION_PLAN.md docs/BASELINE.md docs/superpowers/plans/2026-07-20-stage-0-bootstrap.md
git diff --cached --check
git commit -m "chore: bootstrap personal paper daily project"
```

Expected: commit succeeds with the exact message. If either identity value is absent, do not commit and report that the changes remain staged.

- [ ] **Step 8: Invoke branch-finishing guidance**

Read and follow `superpowers:finishing-a-development-branch`. Because the user forbids a PR in Stage 0, use the option that leaves the verified branch ready for later work; push only if the authenticated private origin was safely established.

- [ ] **Step 9: Produce the required final report**

Use exactly the user's requested top-level headings: Superpowers usage, repository status, baseline results, created/modified files, architecture conclusions, security checks, Git result, and next step. The next-step section contains a complete paste-ready Stage 1 prompt and no Stage 1 implementation claim.
