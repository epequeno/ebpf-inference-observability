# Agent instructions

## What this project is

An experiment in observing **LLM inference servers from the outside using eBPF** — network
hooks, scheduler tracepoints and CUDA API uprobes — without modifying or instrumenting the
server. It is simultaneously a research project and a structured way to learn eBPF and
inference-serving architecture.

Start with `README.md`, then `docs/design.md`. If you are on the target host and something looks
unconfigured, `docs/environment.md` explains why.

## Where this runs

**The target host is a physical Linux box on the LAN.** That machine has the GPU and is where
every probe attaches. macOS cannot run eBPF at any privilege level, so an Apple machine in this
project is an authoring machine only.

You may well be running **on** the target host. Check with `uname -s`. If it says `Darwin`, you
are on the wrong machine for anything involving a probe — say so rather than working around it.

## Read before acting

| File | Why |
|---|---|
| `docs/design.md` | Research question, four-layer model, the five phases and what each must produce |
| `docs/hypotheses.md` | H1–H4 with predictions and **falsification criteria** — the scientific contract |
| `docs/environment.md` | Target host, Phase 0a/0b split, GPU-vs-CPU policy, privileges |
| `docs/results-log.md` | Everything learned so far, newest first. Read it before repeating work. |
| `docs/literature.md` | References and the prior art this sits against |

[`docs/provenance.md`](docs/provenance.md) records which parts of the design are the author's and
which were added later — read it if you need to know how stable a particular claim is.

## Current status and next action

**Phase 0a is complete** (goals met: eBPF toolchain installed, llama.cpp serving on the
GPU, loadgen ground truth, probe attach verified). The committed `inventory/report.md` was
regenerated on 2026-10-10 — the old copy predated the toolchain install and the script fixes.

llama.cpp 0.6.0-dev is built twice, both with CUDA: `build/` (CUDA graphs ON — the
shipping default of 0.6.0-dev) and `build-off/` (`GGML_CUDA_GRAPHS=OFF`, the
author-approved H1 confound control). The **running server is the graphs-OFF binary**
(`build-off/bin/llama-server`, pid 56122 since 2026-10-10T19:48Z; restart with the flags
in the 2026-10-09 results-log entry; readiness regex `llama_server: listening`).
`tools/loadgen.py` produces client-side TTFT/ITL ground truth (plus `--gap` arrival
pacing for bursty-load experiments).

The next milestone is **Phase 1** (`docs/design.md`): per-connection TTFT/ITL from
`tcp_sendmsg` timestamps, validated against that ground truth, with the probe's own
overhead measured (attached vs detached) before any number is quoted. **The sweep has
run** (2026-10-10, `results/phase1/20261010T122431Z/`): ITL confirmed at every
concurrency (≤0.2% median error, r≥0.999) and TTFB confirmed at every C under the
author's same-interval ruling (≤5.9%, r≥0.91) — **H2 is confirmed**; probe cost
within run noise. **Phase 2 has also run** (2026-10-10,
`results/phase2/20261010T123353Z/`): llama.cpp's CPU side is a futex metronome
(99.6% of sleeps in `futex_do_wait`, zero driver wait sites, ~60 futex syscalls per
token) with no visible latency cost at this load; BCC on kernel 7.0 has two
casualties (`profile-bpfcc` compile failure, `offcputime-bpfcc` report hang) —
bpftrace substitutes were used. **Phase 3 has also run** (2026-10-10,
`results/phase3/20261010T192359Z/`): llama.cpp ships CUDA graphs ON by default —
measured both ways (ON: 1 capture of 1,132 kernels then 1 cuGraphLaunch per step;
OFF: 566 cuLaunchKernel per step); **H1 refuted** at the API level (prefill/decode
gap distributions overlap — the asymmetry lives in kernel size, invisible at the
boundary), **H4 confirmed** (bursty arrivals → launch-idle windows tracking the
spacing; continuous batching → 100% activity); graphs collapse the API launch stream
566× and are worth ~12% ITL. Probe cost +0.0% ITL at 190k uprobe events/s.
**Phase 4 has also run** (2026-10-10, `results/phase4/20261010T201207Z/`): fault
censuses under cold caches in both serving configs (-ngl 99 and -ngl 0) show faults
concentrated at LOAD (90%/98%), not first inference — **H3 refuted** (loading is not
lazy: MAP_POPULATE/MADV_WILLNEED prefetch is hardcoded ON in 0.6.0-dev); the warmup
cost on this build is CUDA-graph capture, not weight faulting. KV growth is invisible
in allocations (pools pre-allocated; zero cuMemAlloc under a 16×512-token C=16 run).
**All four hypotheses now have verdicts: H1 refuted, H2 confirmed, H3 refuted, H4
confirmed.** The refutations share one root: llama.cpp 0.6.0-dev has already
engineered away the asymmetries H1/H3 expected to observe (graph replay collapses the
launch stream; prefault collapses the fault timing). What survives is the external
observability itself: TTFB/ITL from sockets, the futex-metronome CPU side, step cadence
and batch amortization. **vLLM has also been introduced** (2026-10-10,
`results/vllm/20261010T202713Z/`): own container from the local vllm-openai image +
the sibling repo's HF cache volume (zero downloads), coexisting with the idle
llama-server. Findings: H1's refutation holds cross-engine (vLLM eager inter-arrival
distributions overlap too); vLLM captures CUDA graphs at startup (llama.cpp at first
request); the graphs collapse is 9×/~0% ITL on vLLM bf16 vs 566×/~12% on llama.cpp Q4;
both engines hide KV behind startup pools (vLLM: 126 startup allocs / 7.72 GiB vs
llama.cpp's 3 / 1.09 GiB — the visible cross-engine contrast); H3's eager half measured
— faults load-concentrated on both engines. The vllm-obs container was stopped and
removed after the runs (image + volume remain; relaunch command in the artifacts'
meta.txt). **The experimental arc (Phases 1–4 + cross-engine) is complete.** Next:
Phase 5 (multi-tenant attribution, optional stretch) or consolidation/write-up.
Runbook: top of `docs/results-log.md`.

## Rules

**Do not install anything without asking.** The inventory script is deliberately read-only.
Package installation on the target host is the author's call.

**Do not change kernel boot parameters, disable secure boot, or alter lock-down state** without
asking. These are disruptive and may need physical access to reverse.

**Measure the probe's overhead before quoting any number it produced.** Run the same workload
attached and detached and report the delta. An unmeasured probe is a confound, not an
instrument. This is a requirement of the design, not a nicety.

**Negative results are results.** Each hypothesis has a falsification criterion stated before
its experiment. If one is refuted, record it plainly and update the status table at the bottom
of `docs/hypotheses.md`. Do not reframe a refutation as a partial success.

**Log everything in `docs/results-log.md`, newest entry at the top**, including failures,
surprises and dead ends. Record exact kernel, driver, server and CUDA versions with any
measurement, and state whether page caches were dropped and whether CUDA graphs were on.

**Aggregate in-kernel.** At high event rates, never stream every event to user space — use BPF
maps and histograms.

## Conventions

- Prefer `bpftrace` for exploration; escalate to CO-RE (`cilium/ebpf` in Go) only when a phase
  needs maps, percentiles or portability. The tooling ladder is in `docs/design.md`.
- When a probe's output contradicts a documented expectation, **report both** and let the author
  decide. Do not silently reconcile a doc with an observation.
- Keep the server under observation unmodified. If you feel the need to patch it to make
  something observable, that is a finding about the limits of external observation — record it
  and stop.
- This repo is **private**. Do not publish it, or make its contents public, without asking.

## Out of scope

- GPU-internal profiling (SM occupancy, tensor-core utilisation) — that is nsys/ncu territory.
  Stop at the CUDA API boundary.
- Cross-server benchmarking for a leaderboard. Comparisons serve understanding mechanisms only.
- Building a production observability product.
