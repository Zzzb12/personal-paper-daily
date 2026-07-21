# Stage 6 Task 1 — Delivery schema contracts

## Changes

- Added frozen, strict Pydantic contracts for `DigestPaper`, `FeishuPayload`,
  `DeliveryRequest`, `DeliveryReceipt`, and `FeishuSettings`.
- Restricted digest cards to Stage 4 `valid` and `eligible` papers and at most
  five papers per payload.
- Validated HTTPS site URLs, Feishu chat identifiers, bounded timeout/retry
  values, lowercase SHA-256 idempotency keys, and rejected Pydantic type
  coercion at the delivery boundary.
- Rejected HTTPS URLs containing embedded user credentials, so credentials
  cannot enter serialized cards or settings.
- Kept credential values out of `FeishuSettings`; environment parsing retains
  only credential variable names and reports missing variable names.
- Added focused offline contract tests. No network calls or `.env` reads were
  introduced.

## RED / GREEN evidence

### RED

Required command:

```powershell
uv run pytest tests/delivery/test_schemas.py -q
```

Result: could not run because `uv` is not available in this shell. A direct
import check then failed as intended before implementation:

```powershell
python -c "import sys; sys.path.insert(0, 'src'); import zotero_arxiv_daily.delivery.schemas"
```

Result: `ModuleNotFoundError: No module named 'zotero_arxiv_daily.delivery'`.

### GREEN

Required command re-run:

```powershell
uv run pytest tests/delivery/test_schemas.py -q
```

Result: still blocked by missing `uv`. Fallback `python -m pytest
tests/delivery/test_schemas.py -q` is blocked before collection because the
local Python environment lacks project dependency `hydra` used by
`tests/conftest.py`.

The test module was loaded directly with the project source path and all five
test functions executed successfully:

```powershell
python -c "import importlib.util, sys; sys.path.insert(0, 'src'); spec=importlib.util.spec_from_file_location('task1_tests', 'tests/delivery/test_schemas.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); [getattr(module, name)() for name in dir(module) if name.startswith('test_')]; print('5 delivery schema tests passed')"
```

Result: `5 delivery schema tests passed` (exit code 0). Python compilation of
all three added files and `git diff --cached --check` also exited 0.

## Independent review

The independent review identified two P1 findings: Pydantic coercion despite
the strict-contract requirement, and acceptance of credential-bearing HTTPS
URLs. Both were reproduced with new failing tests, then resolved by a
delivery-specific `strict=True` frozen base model and URL userinfo rejection.
The direct focused test execution was repeated successfully after the fixes.

## Files

- `src/zotero_arxiv_daily/delivery/__init__.py`
- `src/zotero_arxiv_daily/delivery/schemas.py`
- `tests/delivery/test_schemas.py`

## Self-review

- Every model inherits `StrictModel`, so it is frozen and rejects unknown
  fields.
- Secrets are only checked for presence in the supplied environment mapping;
  secret values are not placed in a model or included in exceptions.
- All validation is local and deterministic; no HTTP client or automation was
  added.
- The staged diff was checked for whitespace errors.

## Concerns

- The shell `PATH` still lacks `uv`, but the supplied shared Stage 0 `uv`
  executable runs the focused pytest file successfully. The full repository
  suite was not run for this narrowly scoped follow-up.

## Credential-error redaction follow-up

### RED

Added regression cases for `DigestPaper` and `FeishuSettings.from_environment`
using the sentinel `REAL_SECRET` in the URL userinfo. Ran:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/delivery/test_schemas.py -q
```

Result: 2 failures, both proving `REAL_SECRET` appeared in `str(exc)` for
Pydantic `ValidationError` instances.

### GREEN

Added model-level pre-validation that replaces credential-bearing `site_url`
input with a fixed non-secret marker before the controlled field rejection.
The rejection remains strict while both `str(exc)` and `exc.errors()` omit the
sentinel credential value. Re-ran the same focused command:

Result: `7 passed in 0.44s`.

## Malformed IPv6 credential URL follow-up

### RED

Added the exact malformed URL `https://user:REAL_SECRET@[::1` to the
credential-redaction parameterized regression test and ran the focused command.

Result: 1 failure (`7 passed, 1 failed`): the sentinel appeared in
`str(exc.errors())` because `urlparse` raised before the prior sanitization.

### GREEN

Replaced pre-validation credential detection with an authority/userinfo regular
expression that does not call `urlparse`; malformed credential URLs are now
redacted before Pydantic constructs a validation error. `_https_url` also
converts `urlparse` `ValueError` into the controlled invalid-HTTPS rejection.

Re-ran:

```powershell
& 'F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe' run pytest tests/delivery/test_schemas.py -q
```

Result: `8 passed in 0.41s`.
