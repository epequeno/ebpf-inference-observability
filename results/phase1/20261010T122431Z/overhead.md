# Probe overhead — attached vs detached (quote no eBPF number without this)
### c=1

| metric | detached | attached | delta |
|---|---|---|---|
| ttft p50 (s) | 0.004 | 0.0037 | -7.5% |
| ttfb p50 (s) | 0.0012 | 0.001 | -16.7% |
| ttft p90 (s) | 0.0046 | 0.0044 | -4.3% |
| itl mean (s) | 0.0028 | 0.0027 | -3.6% |
| itl p50 (s) | 0.0027 | 0.0027 | +0.0% |
| tokens | 512 | 512 | +0.0% |

ok: 16 -> 16  failed: 0 -> 0

### c=4

| metric | detached | attached | delta |
|---|---|---|---|
| ttft p50 (s) | 0.0067 | 0.0068 | +1.5% |
| ttfb p50 (s) | 0.0027 | 0.0029 | +7.4% |
| ttft p90 (s) | 0.0182 | 0.0075 | -58.8% |
| itl mean (s) | 0.0038 | 0.0039 | +2.6% |
| itl p50 (s) | 0.0038 | 0.0038 | +0.0% |
| tokens | 512 | 512 | +0.0% |

ok: 16 -> 16  failed: 0 -> 0

### c=16

| metric | detached | attached | delta |
|---|---|---|---|
| ttft p50 (s) | 0.3738 | 0.374 | +0.1% |
| ttfb p50 (s) | 0.3701 | 0.3704 | +0.1% |
| ttft p90 (s) | 0.3796 | 0.3832 | +0.9% |
| itl mean (s) | 0.0038 | 0.0038 | +0.0% |
| itl p50 (s) | 0.0037 | 0.0037 | +0.0% |
| tokens | 2048 | 2048 | +0.0% |

ok: 64 -> 64  failed: 0 -> 0

### c=64

| metric | detached | attached | delta |
|---|---|---|---|
| ttft p50 (s) | 1.9191 | 1.9667 | +2.5% |
| ttfb p50 (s) | 1.9155 | 1.9629 | +2.5% |
| ttft p90 (s) | 1.9748 | 2.0067 | +1.6% |
| itl mean (s) | 0.0039 | 0.0039 | +0.0% |
| itl p50 (s) | 0.0039 | 0.0039 | +0.0% |
| tokens | 8192 | 8192 | +0.0% |

ok: 256 -> 256  failed: 0 -> 0

