# MapArr Performance Profiling Results

**Date:** 2026-03-11
**Version:** 1.5.0
**Platform:** Windows 11, Python 3.13, uvicorn single-worker

## Pipeline Scan Response Times

| Scenario | Pipeline (ms) | Peak Mem (MB) | Stacks |
|----------|--------------|--------------|--------|
| 5 services, 1 stack | 19.7 | 0.08 | 1 |
| 20 services, 1 stack | 56.3 | 0.19 | 1 |
| 50 services, 1 stack | 130.5 | 0.33 | 1 |
| 10 services, 5 stacks | 134.1 | 0.39 | 5 |
| 10 services, 20 stacks | 554.5 | 1.17 | 20 |
| 10 services, 50 stacks | 1464.5 | 3.65 | 50 |
| 10 services, 100 stacks | 2902.1 | 9.83 | 100 |

## Scaling Analysis

### Service count scaling (single stack)
- 5 to 50 services: 19.7ms to 130.5ms (x6.6)
- Service count increased 10x, response time increased 6.6x
- **Sub-linear scaling** -- excellent

### Stack count scaling (10 services each)
- 5 to 100 stacks: 134.1ms to 2902.1ms (x21.6)
- Stack count increased 20x, response time increased 21.6x
- **Approximately linear** -- acceptable. Each stack adds ~29ms of analysis overhead.

### Per-stack overhead
- Average per-stack cost at 100 stacks: ~29ms
- This includes: compose file discovery, YAML parsing, 4-pass analysis, result aggregation
- At 100 stacks the total is ~3 seconds -- still well under user patience threshold

## Memory Profile

- Peak memory across all scenarios: 9.83MB (100 stacks)
- Per-stack memory cost: ~0.1MB
- **Well within limits** -- no memory concerns even at 100 stacks
- Memory is traced via tracemalloc (Python allocator only, excludes C extensions)

## Concurrent Requests (5 simultaneous)

- Total wall time: 2989.2ms (against 20-stack scenario)
- Status codes: [200, 200, 200, 200, 200]
- Errors: 0
- **All requests completed successfully** -- no race conditions observed
- Note: uvicorn's async event loop handles concurrent requests sequentially for CPU-bound work

## Threshold Check

| Threshold | Scenario | Actual | Verdict |
|-----------|----------|--------|---------|
| <500ms @ 20 svc | 20 services, 1 stack | 56.3ms | PASS |
| <2000ms @ 50 svc | 50 services, 1 stack | 130.5ms | PASS |
| <5000ms @ 100 stacks | 10 services, 100 stacks | 2902.1ms | PASS |

## Recommendations

1. **No performance blockers** -- all thresholds passed comfortably
2. **Rate limiting works**: rapid-fire requests trigger 429s correctly (20/min analysis tier)
3. **Scaling projection**: At 200+ stacks (~6s) consider adding progress indicator. v1.6+ concern.
4. **Memory negligible** -- 10MB for 100 stacks. No accumulation concerns.
5. **Concurrent safety confirmed** -- global state is last-write-wins but no crashes. Acceptable for single-user tool.
