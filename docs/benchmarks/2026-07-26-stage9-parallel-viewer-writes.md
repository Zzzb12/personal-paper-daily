# Stage 9 parallel viewer write evidence

## Decision

The bounded parallel atomic viewer writer passes the measured Stage 9 gate with 8 workers. The final decision uses 31 alternating AB/BA pairs, fresh output roots, identical fixture preparation, identical allocation tracing, and no profiler inside timed regions.

| Measure | Sequential reference | Parallel writer | Result |
| --- | ---: | ---: | ---: |
| Median | 197,259,800 ns | 173,691,300 ns | 11.9479% faster |
| p95 | 223,178,000 ns | 190,794,900 ns | 85.4900% of reference |
| Peak traced allocation | 1,806,314 bytes | 1,768,730 bytes | no regression |

The complete sorted distributions and the shared semantic equivalence hash are recorded in the adjacent JSON evidence.

## Measurement history

The first formal CLI observation used 15 对 and produced only 8.0858% median improvement, so it failed the unchanged 10% gate. No threshold was relaxed and that failure is retained in the JSON. Because an earlier exploratory observation conflicted with it, the final run expanded the same alternating design to 31 对. The larger sample passed at 11.9479% median improvement and a p95 ratio of 85.4900%.

## Semantic and safety audit

- Both variants exercised the same 30 candidates, 15 ranking selections, 5 full-analysis selections, and 5 reviewed viewer publications.
- Quality budgets and fixture selection labels passed.
- The paired runner rejected any difference in viewer artifact identity, ranking, quality, cost, cache, retry, partial-failure, and stage-result semantics before accepting timing.
- Network and paid-call counters were zero. No real library, model, Feishu, or hosted GitHub operation was performed.
- Optimized steady-state profiling returned `no_eligible_target`: no remaining project row met the unchanged 20% target threshold.

## Frontend boundary

Frontend Design, GSAP Core, and GSAP Performance guidance were checked before implementation. This change only schedules already-rendered independent atomic file writes; it does not change HTML, CSS, JavaScript, DOM structure, motion, CSP, or interaction behavior. Adding a GSAP runtime would therefore add bytes and execution cost without a visual requirement, so no GSAP dependency was introduced.

## Reproduction and scope

The evidence was collected locally on Windows with Python 3.13 using repository fixtures and the versioned comparison CLI. It demonstrates this fixture workload and does not predict hosted runner, real PDF, or network performance. Raw run roots remain under the ignored `outputs/stage9/` directory.
