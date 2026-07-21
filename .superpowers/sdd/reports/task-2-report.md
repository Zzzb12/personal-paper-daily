# Stage 6 Task 2 Report — Validated Feishu Card Rendering

## Delivered scope

- Added `DigestPolicy.build(batch, *, chat_id, site_url)` to turn only Stage 4
  `validated` / `valid` / `eligible` results into a bounded `DeliveryRequest`.
- Added `FeishuRenderer.render(payload)` to return deterministic, offline
  interactive-card JSON with Chinese title, English title, and safe site link
  fields in that order.
- Kept the implementation pure: it does not read `.env`, construct a network
  client, log values, or modify Stage 7/8 behavior.

## RED / GREEN evidence

### RED

Command:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/delivery/test_feishu_renderer.py -q
```

Observed result: collection failed with the expected
`ModuleNotFoundError: No module named 'zotero_arxiv_daily.delivery.feishu'`.
This confirmed the newly written renderer-policy tests exercised the missing
production boundary rather than an unrelated failure.

### GREEN

Command:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/delivery/test_feishu_renderer.py tests/analysis/test_validator_rules.py -q
```

Observed result: `30 passed`.

Final focused verification command:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/delivery/test_schemas.py tests/delivery/test_feishu_renderer.py tests/analysis/test_validator_rules.py -q
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run python -m compileall -q src\zotero_arxiv_daily\delivery
```

Observed result: `40 passed`; compilation exited `0`.

## Files

- Added `src/zotero_arxiv_daily/delivery/feishu.py`.
- Added `tests/delivery/test_feishu_renderer.py`.
- Added this report.

## Self-review

- `DigestPolicy` preserves batch order, requires Stage 4 `validated` plus
  report `valid` / `eligible`, and stops after five papers even if a schema
  bypass supplies more.
- The idempotency key is a SHA-256 hash of a canonical payload serialization.
- Renderer output has a fixed Feishu interactive-card JSON structure and uses
  deterministic JSON serialization.
- Text is HTML/Markdown escaped; absent Chinese titles and omitted unsafe
  links render the controlled fallback `论文未明确提供`.
- URL rendering only permits HTTPS URLs with no parsed username or password;
  credential-bearing and non-HTTPS values are not copied into card text.
- `git diff --check` reported no whitespace errors. Source inspection found no
  `.env`, environment, HTTP, or network client access.

## Review and concerns

- An independent read-only review was requested twice, but neither reviewer
  returned within the available time window; no findings were received to
  resolve. The focused tests and self-review above are the available evidence.
- The full repository test suite was intentionally not run for this bounded
  task; the specified renderer, delivery-contract, and Stage 4 regression
  suites were run. No known functional concern remains within Task 2 scope.

## Review follow-up

Independent review identified three Task 2 gaps. They were addressed with a
new RED/GREEN cycle before this follow-up commit:

- **Markdown injection:** dynamic fields now collapse all whitespace to one
  line, HTML-escape text, and escape Markdown structural characters. A
  schema-bypassed title containing heading, quote, list, and table markers is
  asserted not to emit raw block structure.
- **Design-required facts:** `DigestPaper` now carries only validated
  recommendation reason, first core Insight, strongest supporting visual label
  and page, and first experimental conclusion. The policy reads these solely
  from `result.validated.analysis`; missing facts use `论文未明确提供` in the
  renderer.
- **Schema-bypass identity:** policy rejects a result unless its outer paper
  ID, validated analysis ID, and report ID agree and the validated report is
  the exact report object attached to the outer result.

RED command:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/delivery/test_schemas.py tests/delivery/test_feishu_renderer.py -q
```

Observed result: `7 failed, 9 passed`, for the expected missing strict fields,
missing card sections, raw Markdown block injection, and invalid identity
inclusion.

GREEN command:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/delivery/test_schemas.py tests/delivery/test_feishu_renderer.py tests/analysis/test_validator_rules.py -q
```

Observed result: `42 passed`. The follow-up remains offline and does not read
`.env` or add network behavior.

During final self-review, Markdown escaping was applied before HTML escaping
so quote entities remain intact; the malicious-input regression was rerun and
the same focused suite still reported `42 passed`.

## JSON hydration follow-up

Re-review found that the prior report-object identity check rejected a normal
`ValidationBatchResult` after `model_dump_json()` / `model_validate_json()`.
The policy now rejects only semantically unequal reports while retaining the
outer result, validated analysis, and outer report paper-ID invariants.

RED: the new JSON round-trip regression produced an empty hydrated payload
while the source batch retained one paper. GREEN: the source and hydrated
batches each retain one paper; the hydrated reports are non-identical but
equal. Final focused command (delivery contracts, renderer, Stage 4 rules)
reported `43 passed`; delivery compilation also exited successfully. No
environment read or network behavior was added.
