# Personal Paper Daily README Redesign

## Goal

Replace the inherited upstream README with a project-specific public landing
page for Personal Paper Daily. The README must explain the implemented product
accurately, help a new user start safely, and retain honest upstream
attribution without presenting upstream badges, sponsorship, screenshots, or
legacy email instructions as this project's own.

## Audience and language

- Primary audience: Chinese-speaking researchers who use Zotero and want a
  daily paper recommendation and reading workflow.
- Primary language: Chinese.
- The title is followed by one short English summary for international
  visitors.
- Established technical names such as Zotero, arXiv, Docling, DeepSeek,
  GitHub Actions, and Feishu remain in English.

## Page structure

1. Project title and concise English summary.
2. Chinese product summary and safety defaults.
3. Core capabilities covering interest modeling, candidate ranking, PDF
   parsing, evidence-bound Chinese analysis, publication validation, static
   viewer, Feishu delivery, feedback, automation, and observability.
4. A compact end-to-end flow.
5. Requirements and installation.
6. Environment configuration with variable names only.
7. Safe local quick start:
   - offline fixture dry-run;
   - live Zotero and LLM run without Feishu;
   - explicit Feishu send command.
8. GitHub Actions configuration, distinguishing Secrets from Variables and
   documenting the exact live/send/deploy acknowledgement gates.
9. Output locations, tests, documentation links, privacy boundaries, known
   limitations, license, and upstream acknowledgement.

## Content rules

- Remove badges and links that refer to the upstream repository's Stars,
  Issues, Pull Requests, sponsors, donations, and Star History.
- Remove obsolete SMTP/email deployment instructions and upstream screenshots.
- Do not include a real credential, token-shaped example, private Zotero path,
  generated paper result, or user-specific URL.
- Use empty or descriptive placeholders for environment variables.
- State that dry-run/no-send is the default and that live LLM execution can
  incur cost.
- State that Feishu delivery requires the exact `--send-feishu` flag.
- State that the public viewer contains only reviewed artifacts and that
  `.env`, raw Zotero data, caches, feedback state, PDFs, and credentials must
  remain private.
- Do not claim every test passes on Windows. Link to the documented baseline
  for the two one-second multiprocessing spawn failures and the optional slow
  Hugging Face dependency.
- Keep detailed architecture and stage history in existing `docs/` files
  rather than duplicating them in the README.

## Attribution and licensing

- Identify this repository as an independently extended personal workflow
  derived from `TideDra/zotero-arxiv-daily`.
- Link to the upstream repository and summarize its foundational contribution
  without implying that upstream maintains this fork.
- Retain the repository's AGPLv3 license statement and link to `LICENSE`.

## Scope and verification

Only `README.md` is changed during implementation. Verification consists of:

- checking all documented commands and variable names against the current CLI,
  workflow, `.env.example`, and configuration;
- scanning the README for obsolete SMTP/upstream sponsorship instructions,
  credential-like strings, placeholders, and broken local links;
- rendering review through GitHub-compatible Markdown structure;
- running `git diff --check` and confirming the worktree contains only the
  intended documentation change.
