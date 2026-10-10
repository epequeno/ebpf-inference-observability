# Phase 2 probe overhead — quote no probe-derived number without this

Load for every comparable run: loadgen C=16, 256 requests, 64 tokens (identical);
control run detached. Summary metrics from loadgen ground truth.

| run | probe | ttft p50 s | itl mean s | ok | wall s |
|---|---|---|---|---|---|
| control | none | 0.7356 | 0.0037 | 256 | 15.6 |
| e1 | bpftrace tools/phase2-threads.bt (sched+futex+glibc census) | 0.7321 | 0.0037 | 256 | ~16 |
| e2 (attempt 3) | offcputime-bpfcc -p (single instance) | 0.7251 | 0.0036 | 256 | 16.0 |
| e2 (attempt 2) | TWO offcputime instances (operator error, kept as data) | 0.7377 | 0.0037 | 256 | ~110 |
| e2c | bpftrace block-site census | 0.7376 | 0.0037 | 256 | ~16 |
| e4 | bpftrace profile:hz:49 | 0.7328 | 0.0037 | 256 | ~16 |

- e2b used a 64-request load (offcputime -K report-retry) — different load, listed for
  completeness, not comparable to the 256-request control.
- All single-probe runs sit within ±1.5% of control on ttft p50 and flat (3.6–3.7 ms)
  on itl mean: no measurable probe cost for the bpftrace censuses or a single
  offcputime attachment.
- The only distortion observed was the double attachment (attempt 2): wall ≈7x with
  per-request metrics unchanged — the cost appeared between requests, not within them.
- offcputime's own reports were lost to the report-path hang (see results-log):
  its measured cost above stands, but it produced no usable profile at this event rate.

## Scope of this overhead measurement (added 2026-10-10, per Craun et al. 2024)

Per Craun et al., "Eliminating eBPF Tracing Overhead on Untraced Processes" (SIGCOMM
2024 eBPF workshop), tracing overhead lands partly on untraced co-located processes —
here the loadgen client is one (same host). The table above bounds user-visible cost,
not total system cost. The double-attachment row is the clearest local demonstration:
its ~7x wall inflation appeared *between* requests while per-request metrics stayed
flat.
