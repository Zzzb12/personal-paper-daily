# Stage 6 Task 3 review-fix report

## Status

Resolved every requested Critical/Important Task 3 review finding without adding
Stage 7/8 behavior. All tests used injected fakes or the offline Stage 4 fixture.
No `.env` file was read, no real HTTP transport was used, and no Feishu message
was sent.

## Root causes

- `argparse.ArgumentParser` retained its default prefix abbreviation behavior,
  so `--sen` and `--se` activated `--send`.
- The client relied only on its in-process receipt ledger and omitted Feishu's
  request-level message `uuid` field.
- The CLI wrote the preview before branching between preview and send modes.
- The CLI surfaced the full Pydantic validation error for complete but invalid
  environment configuration.
- Preview output used a direct `Path.write_text` and did not reject a fixture
  path that resolved to the output path.
- `Retry-After` only handled numeric strings and accepted non-finite floats.

## TDD evidence

### RED

After adding regression tests, the fixed uv command was:

```powershell
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run pytest tests/delivery/test_feishu_client.py tests/pipeline/test_feishu.py -q
```

Observed result: `12 failed, 14 passed`. The failures directly demonstrated the
missing message UUID, UUID reuse gap, unsupported HTTP-date, non-finite retry
delays, leaked Pydantic configuration details, send-mode preview write, missing
same-path rejection, and missing atomic preview operations.

The abbreviation test was then strengthened with a complete synthetic
environment, because missing settings had initially masked the parser bug. The
focused RED command produced `2 failed, 7 deselected`; both `--sen` and `--se`
constructed the transport instead of being rejected as unknown.

Permanent 400/403/404/422 tests passed during RED. They document that the
existing permanent-4xx no-retry behavior was correct and close the prior test
coverage gap.

### GREEN

The same focused command passed: `26 passed in 0.89s`.

Minimal production changes:

- disabled argparse abbreviation and kept unknown arguments above all send
  configuration/transport work;
- derived a deterministic UUIDv5 from `DeliveryRequest.idempotency_key`, added
  it only to the Feishu message POST body, and reused it across retries;
- separated preview and send side effects so successful send mode never writes
  the requested preview path;
- mapped invalid complete configuration to the fixed CLI error
  `invalid Feishu delivery configuration`;
- rejected fixture/output paths with equal resolved paths;
- wrote previews through a same-directory named temporary file followed by
  flush, `fsync`, and `os.replace`, with temporary-file cleanup on failure;
- accepted bounded Retry-After HTTP dates and fell back for non-finite numeric
  values.

## Verification

All commands used the fixed uv executable.

- Focused Task 3: `26 passed in 0.89s`.
- Delivery + Feishu pipeline + Stage 4 validator regression:
  `69 passed in 2.21s`.
- `python -m compileall -q src tests`: exit 0.
- `git diff --check`: exit 0; only the repository's Windows line-ending
  warnings were emitted.
- Offline preview command: exit 0, `papers=1 sent=false`; JSON parsed with
  `msg_type=interactive`, and no matching temporary file remained.
- Full diagnostic suite with `--import-mode=importlib`:
  `469 passed, 2 failed, 1 deselected in 15.97s`. The two failures are the
  unchanged Windows one-second `multiprocessing spawn` hard-timeout baseline
  cases documented in `AGENTS.md`:
  `test_run_with_hard_timeout_returns_value` and
  `test_run_with_hard_timeout_returns_none_on_failure`.

## Safety and review

- CLI send success was exercised only with a fully synthetic environment and
  an injected fake transport. It made exactly the fake token and message calls,
  closed the fake transport, and did not create the preview file.
- The UUID tests assert stable reuse across a retry, RFC UUID validity, UUIDv5,
  and the Feishu maximum-length requirement (`36 <= 50`).
- Invalid chat ID and credential-bearing site URL sentinels are both absent
  from captured stderr, which also contains no Pydantic `input_value` detail.
- An independent review was requested through the mandated review workflow,
  but all agent slots were occupied. No reviewer was available; the parent task
  should perform the final independent diff review when a slot is released.

## Concerns

- Live Feishu behavior remains deliberately untested. The request shape and
  UUID semantics are verified only through injected fakes.
- The receipt ledger remains process-local as designed; the Feishu UUID adds
  server-side retry deduplication but is not a durable application ledger.
- The known Windows retriever timeout baseline and default pytest module-name
  collision are outside Stage 6 and were not changed or suppressed.
