# Stage 6 Task 3 implementation report

## Status

Implemented the injected Feishu client and the fixture-only preview CLI from
`.superpowers/sdd/briefs/task-3-brief.md`. No real Feishu request was made, no
`--send` command was run, and no `.env` file was read.

## TDD evidence

### RED 1

Command (using the required fixed uv executable):

```powershell
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run pytest tests/delivery/test_feishu_client.py tests/pipeline/test_feishu.py -q
```

Observed result: collection failed with two expected import errors because
`FeishuClient` and `zotero_arxiv_daily.pipeline.feishu` did not yet exist.

### GREEN 1

After the minimal client, retry policy, in-process receipt ledger, and CLI gate
were implemented, the same command passed: `8 passed in 0.70s`.

### RED 2

Self-review found two contract gaps and added focused tests before changing
production code:

- the Feishu message API must receive the card object JSON in `content`, not
  the outer preview envelope;
- only HTTP 5xx is retryable, so status 600 must not be retried.

The two focused tests failed for those exact reasons: the outgoing `content`
still contained the outer envelope, and status 600 exhausted the fake outcome
queue because it was incorrectly retried.

### GREEN 2

After extracting and canonicalizing the card object and restricting retry to
429 or 500-599, the final Task 3 focused suite passed: `9 passed in 0.66s`.

## Implemented behavior

- `FeishuClient(settings, transport)` obtains a tenant token and sends an
  interactive message through the injected transport.
- Credentials are resolved only from the provided environment mapping (the CLI
  passes `os.environ`); they are not retained in delivery models or receipts.
- HTTP calls carry the request timeout. Only 429 and 500-599 are retried, with
  at most `1 + max_retries` attempts and bounded delay.
- 401 and all other permanent statuses fail without retry. Remote response
  bodies and underlying timeout/transport exception text are not copied into
  public exception messages.
- A successful receipt is cached per idempotency key in the client instance, so
  a repeated key does not fetch a second token or send a second message.
- The CLI's default path validates the offline fixture, writes a deterministic
  preview, and returns without constructing `HttpxTransport`.
- Literal `--send` is required before settings are loaded for sending. Missing
  settings are rejected by argparse before a transport is constructed.

## Verification

All commands below used the fixed uv executable.

- Task 3 focused: `9 passed in 0.66s`.
- Delivery + Task 3 pipeline + Stage 4 validator regression:
  `52 passed in 1.75s`.
- `python -m compileall -q src tests`: exit 0.
- Required preview command: exit 0 and printed
  `preview=outputs\feishu-preview.json papers=1 sent=false`.
- The generated preview parsed as interactive-card JSON and stayed under the
  ignored `outputs/` directory.
- `git diff --check`: clean (Git only emitted the repository's Windows line
  ending warning).
- Changed-file sizes: largest file 14,267 bytes; no large generated artifact is
  included.
- Targeted secret scan: no private-key or bearer-token literal. One assignment
  rule hit is the deliberately synthetic credential sentinel in the fake-client
  test, not a real credential. No matched value is included in this report.

### Full-suite baseline findings

Configured `pytest -q` does not collect the full repository because the
pre-existing un-packaged files `tests/delivery/test_schemas.py` and
`tests/viewer/test_schemas.py` collide as the top-level module `test_schemas`.
The issue reproduces with only those two existing files and is outside Task 3.

For diagnostic full coverage, `pytest -q --import-mode=importlib` ran 454 tests:
`452 passed, 2 failed, 1 deselected`. The two failures are exactly the Windows
`multiprocessing` one-second hard-timeout cases documented in `AGENTS.md`:

- `tests/retriever/test_arxiv_retriever.py::test_run_with_hard_timeout_returns_value`
- `tests/retriever/test_arxiv_retriever.py::test_run_with_hard_timeout_returns_none_on_failure`

No test was skipped, weakened, or modified to hide either baseline issue.

## Files

- Modified: `src/zotero_arxiv_daily/delivery/feishu.py`
- Created: `src/zotero_arxiv_daily/pipeline/feishu.py`
- Created: `tests/delivery/test_feishu_client.py`
- Created: `tests/pipeline/test_feishu.py`
- Created: `.superpowers/sdd/reports/task-3-report.md`

The parent-owned untracked brief and progress files were not modified or staged.

## Self-review

- Brief coverage: token/send success, idempotency, 401 no retry, bounded 429/5xx
  retry, timeout redaction, secret-safe errors, preview output, preview zero
  transport construction, and send-setting gate are all directly tested.
- Safety: default preview is zero network; production transport construction is
  below the explicit send gate; all tests use an injected fake; no real send or
  external request occurred.
- Scope: no Stage 7/8 behavior, unrelated refactor, schema change, or baseline
  test workaround was added.
- Output contract: preview preserves the renderer envelope; API send extracts
  only the card object for the Feishu `content` string.
- Error contract: public delivery errors contain controlled categories/status
  codes only, never remote bodies or chained transport text.

## Concerns and handoff notes

- The in-process idempotency ledger is intentionally per-client and non-durable,
  matching the Task 3 brief; persistence belongs outside this task.
- Live Feishu behavior was not exercised. Request shape is tested only through
  the injected fake, as required by the safety boundary.
- Independent review could not be spawned because all agent slots were occupied.
  The root agent explicitly instructed this task to finish locally and will
  arrange an independent Task 3 review after a slot is released.
- The default pytest collection collision and the two documented Windows timeout
  failures remain unchanged and should be handled at the stage-level handoff.
