# Stage 9 parallel viewer write evidence

## Confirmed decision

The bounded parallel atomic viewer writer passes the independent Stage 9C confirmation protocol with 8 workers. Protocol commit `77fc731` fixed one and only one 99 对 alternating AB/BA run before the confirmation output existed. It prohibited optional stopping, sample extension and rerunning for a favorable result.

| Measure | Sequential reference | Parallel writer | Result |
| --- | ---: | ---: | ---: |
| Median | 234,164,600 ns | 208,970,900 ns | 10.7590% faster |
| p95 | 259,528,100 ns | 231,485,000 ns | 89.1946% of reference |
| Peak traced allocation | 2,060,272 bytes | 2,013,396 bytes | no regression |

The tracked JSON records min, q1, median, q3, p95 and max for both distributions, the shared semantic equivalence hash, and the SHA-256 of the ignored canonical raw result. The 99-pair confirmation is the only result used for the final verdict.

## Historical observations

The original Stage 9B formal CLI observation used 15 对 and produced only 8.0858% median improvement, so it failed the predeclared 10% gate. The subsequent 31 对 run passed at 11.9479%, but it was collected after observing the failure and is classified as exploratory non-verdict evidence. Neither historical result was used to choose the fixed Stage 9C sample size after confirmation data collection began.

## Semantic and safety audit

- Both variants exercised the same 30 candidates, 15 ranking selections, 5 full-analysis selections, and 5 reviewed viewer publications.
- Quality budgets and fixture selection labels passed.
- The paired runner rejected any difference in viewer artifact identity, ranking, quality, cost, cache, retry, partial-failure, and stage-result semantics before accepting timing.
- Network and paid-call counters were zero. No real library, model, Feishu, or hosted GitHub operation was performed.
- Optimized steady-state profiling returned `no_eligible_target`: no remaining project row met the unchanged 20% target threshold.
- Review repairs moved stale cleanup before the final manifest write, restored caller tracemalloc state on normal and exceptional paths, rejected output-root ancestor symlink/junction/reparse points, and included relative source paths in code identity.

## Frontend boundary

Frontend Design, GSAP Core, and GSAP Performance guidance were checked before implementation. This change only schedules already-rendered independent atomic file writes; it does not change HTML, CSS, JavaScript, DOM structure, motion, CSP, or interaction behavior. Adding a GSAP runtime would therefore add bytes and execution cost without a visual requirement, so no GSAP dependency was introduced.

## Reproduction and scope

The confirmation was collected locally on Windows with Python 3.13 using repository fixtures and the versioned comparison CLI. It demonstrates this fixture workload and does not predict hosted runner, real PDF, or network performance. Raw run roots remain under the ignored `outputs/stage9/confirmation-99/` directory.
