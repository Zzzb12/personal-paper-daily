# Personal Paper Daily README Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inherited upstream README with an accurate, Chinese-first public landing page for Personal Paper Daily.

**Architecture:** Keep `README.md` as a concise product and operations entry point, while linking detailed product, architecture, baseline, and roadmap material from `docs/`. Derive every command, environment variable, safety gate, and limitation from the current CLI, workflow, and tracked configuration rather than from upstream instructions.

**Tech Stack:** GitHub-flavored Markdown, PowerShell, Python 3.13, uv, GitHub Actions.

## Global Constraints

- The primary language is Chinese with one short English introduction near the title.
- Only `README.md` changes during implementation.
- Do not include credentials, token-shaped examples, private Zotero data, generated paper results, or user-specific URLs.
- Remove upstream badges, sponsorship, donation, screenshot, Star History, and SMTP/email instructions.
- State that dry-run/no-send is the default and live LLM execution can incur cost.
- Retain honest upstream attribution and the repository's AGPLv3 license statement.
- Do not claim the complete Windows test suite is green.

---

### Task 1: Replace the inherited README

**Files:**
- Modify: `README.md`
- Reference: `.env.example`
- Reference: `.github/workflows/personal-paper-daily.yml`
- Reference: `src/zotero_arxiv_daily/pipeline/daily.py`
- Reference: `docs/PRODUCT_SPEC.md`
- Reference: `docs/ARCHITECTURE.md`
- Reference: `docs/BASELINE.md`
- Reference: `docs/IMPLEMENTATION_PLAN.md`

**Interfaces:**
- Consumes: the current unified daily CLI, environment-variable contract, workflow gates, and documented baseline.
- Produces: a GitHub landing page that routes users to safe local execution, GitHub Actions deployment, and detailed project documentation.

- [ ] **Step 1: Verify command and configuration names**

Run:

```powershell
Select-String -Path '.env.example' -Pattern '^[A-Z][A-Z0-9_]+='
Select-String -Path '.github/workflows/personal-paper-daily.yml' -Pattern 'PAPER_DAILY_|workflow_dispatch|schedule|send_feishu|live_run'
.\.venv\Scripts\python.exe -m zotero_arxiv_daily.pipeline.daily --help
```

Expected: the README uses only variables, gates, and CLI options shown by these sources.

- [ ] **Step 2: Rewrite `README.md`**

Replace the inherited content with these sections in order:

```text
Personal Paper Daily
Short English summary
项目简介
核心能力
处理流程
快速开始
环境变量
本地运行
GitHub Actions
输出与数据边界
测试与已知限制
项目文档
许可证与上游致谢
```

The quick start must include:

```powershell
uv sync --frozen
uv run python -m zotero_arxiv_daily.pipeline.daily --trigger local --mode dry-run --offline-fixture tests/fixtures/evidence/stage4_golden.json
```

The live examples must use `python-dotenv`, unique run IDs, and omit
`--send-feishu` from the first cost-bearing run. The Feishu example adds only
the exact `--send-feishu` flag.

- [ ] **Step 3: Audit obsolete and unsafe content**

Run:

```powershell
Select-String -Path 'README.md' -Pattern 'SENDER_PASSWORD|smtp|Buy Me A Coffee|Star History|api.gitsponsors|wechat_sponsor'
Select-String -Path 'README.md' -Pattern 'sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|BEGIN .*PRIVATE KEY'
```

Expected: both commands return no matches.

- [ ] **Step 4: Validate local links and documented commands**

Check every relative Markdown link target in `README.md` exists. Run the CLI
help command and the offline fixture command exactly as documented.

Expected: all local links resolve, CLI help exits `0`, and the fixture dry-run
exits `0` without network, paid calls, or sending.

- [ ] **Step 5: Review and commit**

Run:

```powershell
git diff --check
git diff -- README.md
git status --short
git add README.md
git commit -m "docs: replace inherited README"
```

Expected: the diff contains only the intended project-specific README rewrite,
and the commit succeeds.

- [ ] **Step 6: Push and verify GitHub**

Run:

```powershell
git push origin HEAD:refs/heads/main
gh api 'repos/Zzzb12/personal-paper-daily/contents/README.md?ref=main' --jq '{path,sha,size}'
git ls-remote origin refs/heads/main
```

Expected: remote `main` matches local `HEAD`, and GitHub returns the README
metadata from the default branch.
