# Findings — the consolidated narrative

*Assembled 2026-10-10, after the experimental arc completed: Phases 1–4 plus the vLLM
cross-engine leg, all on saturn (kernel 7.0.0-38-generic, RTX 4070 Ti SUPER, driver
595.99.02). Every number below traces to committed artifacts under `results/` and a
`docs/results-log.md` entry. This is the design's "distinctive artifact": one coherent,
externally-observed narrative of what an inference server does, layer by layer — with the
server never modified, rebuilt, or instrumented.*

## Scoreboard

| Hypothesis | Claim | Verdict | Evidence in one line |
|---|---|---|---|
| H1 | Prefill/decode separable from launch signatures | **refuted, cross-engine** | inter-launch gaps overlap on llama.cpp graphs-off (~99.9% < 20 µs, both phases) *and* vLLM eager (81% vs 77% < 20 µs) — same median bucket |
| H2 | TTFB & ITL recoverable from `tcp_sendmsg` alone | **confirmed** | median error 5.9/4.9/0.0/0.0% (TTFB) and 0.1% (framing-symmetric ITL) at C=1/4/16/64; Pearson r ≥ 0.91 / 0.999 |
| H3 | Lazy mmap loading explains warmup | **refuted** | faults concentrate at LOAD on both engines (90–98% llama.cpp, 94.6% vLLM); llama.cpp prefaults by design |
| H4 | Idle gaps visible as launch gaps; batching removes them | **confirmed** | bursty 1 s arrivals → 23% activity with idle windows at the spacing; continuous → 100% activity |

**The root cause of the two refutations:** both engines have *already engineered away* the
asymmetries H1/H3 expected to observe. CUDA graph replay collapses the per-kernel launch
stream; prefetching (MAP_POPULATE / MADV_WILLNEED, hardcoded) collapses lazy weight
loading. The pre-registered falsification criteria caught both — the "negative results
are results" framing did its job.

## Layer 1 — Network service (Phase 1; `results/phase1/`)

Request-level latency is recoverable from socket events alone, validated against an
independent client. Two definitional findings shaped the metric:

- The probe's first send is the **HTTP-header write**, not the first token; the
  header→token gap is state-dependent (3–35 ms measured). First-token TTFT is therefore
  *not recoverable from send timing at low load* — recorded as a limit of external
  observation; H2's clause was redefined (author decision) to time-to-first-byte.
- **Under load, the raw TTFT metric heals itself**: error 76.8% at C=1 collapses to
  0.2% at C=64, because queueing makes the header flush ride with the first token.
- Queueing is externally visible as a **TTFT staircase** (one request-duration per wave at
  C=16); across the sweep TTFT p50 degrades 3.7 ms → 1.97 s (532×) while ITL moves only
  2.7 → 3.9 ms (+44%) — the prefill/decode split, measured from the network layer.
- Probe cost: within run noise at every concurrency (the cross-cutting requirement).

## Layer 2 — Scheduler / CPU side (Phase 2; `results/phase2/`)

llama.cpp's CPU side is a **futex metronome, not a GPU-waiter**:

- 99.6% of 396k sleeps land in `futex_do_wait`; **zero** NVIDIA-driver wait sites during
  sustained GPU decode; ~60 futex syscalls and ~24 sleep/wake cycles per token.
- 99.97% of wakeups arrive from interrupt (timer) context — nearly every explicit
  `FUTEX_WAKE` found an empty wait queue (timed waits expire first).
- Every on-CPU slice < 250 µs; 97.4% of on-CPU time is user mode (unattributable without
  symbols — the shipped binary is stripped; a symbol-rich rebuild remains an author call).
- None of this shows in user-visible latency (ITL flat) — orchestration overhead that
  exists but does not yet cost anything at this load.
- BCC on kernel 7.0 has two casualties (`profile-bpfcc` compile failure;
  `offcputime-bpfcc` report-generation hang at load-event volumes) — bpftrace
  substitutes delivered both measurements, and the design's CO-RE escalation point for
  off-CPU work moved earlier than planned.

## Layer 3 — GPU boundary (Phase 3 + vLLM; `results/phase3/`, `results/vllm/`)

- **An idle server makes zero CUDA API calls** — a clean black-box idle signature.
- **llama.cpp 0.6.0-dev ships CUDA graphs ON** (`GGML_CUDA_GRAPHS` CMake default, no
  runtime switch). Capture happens at the *first inference* per shape: one 1,132-kernel
  burst (the whole DAG, submitted once, all gaps < 20 µs), then one `cuGraphLaunch` per
  step with **zero** parameter updates — per-step state lives in device memory.
- **vLLM captures at startup** instead — the opposite warmup point, and the explanation
  for its first-request fault burst being machinery rather than weights.
- **Graphs economics, cross-engine and externally visible:** llama.cpp collapses the API
  launch stream **566×** (566 launches/step → 1) and gains **~12% ITL** (tiny Q4 kernels
  are launch-bound); vLLM collapses it **9×** (321 → 1 replay + ~34 side launches) with
  **~0% ITL gain** (bf16 kernels are too big for launch overhead to matter). Same
  mechanism, opposite latency outcomes — "why CUDA graphs help small-kernel models",
  measured from outside on two engines.
- **H1 refuted on both engines:** submission is CPU-bound in both phases even with
  ms-scale prefill kernels; what separates prefill from decode is step *structure*
  (vLLM eager: 656 launches per 600-token forward in ~26 ms vs 321 per decode step in
  5.8 ms) — which requires request context, not the inter-arrival distribution H1 specified.
- **H4 confirmed:** bursty arrivals leave launch-idle windows tracking the spacing;
  continuous batching eliminates them; and batching amortizes launches per token
  (566/token at C=1 → 204/token at C=16 on llama.cpp).

## Layer 4 — Memory system (Phase 4 + vLLM; `results/phase4/`, `results/vllm/`)

- **Loading is not lazy anywhere.** llama.cpp faults 90% (GPU config) / 98% (CPU config)
  of its pages **at load** — prefetch is hardcoded (`init_mappings(true)` → MAP_POPULATE
  or MADV_WILLNEED); the GPU path then drops the file pages after the VRAM upload
  (MADV_DONTNEED, RssFile 270 MB) while the CPU path keeps them resident (1.22 GB).
  vLLM (eager loading) is 94.6% at startup. The classic mmap-vs-eager fault distinction
  has collapsed across modern engines; the *warmup* request's real cost on this build is
  the CUDA-graph capture, not weight faulting.
- First-inference residues are machinery, not weights: llama.cpp ~3.3k faults (≈5×
  steady), vLLM 27k (≈3.9k× its 7-fault steady state) — tokenizer/sampling/detok pages.
- **KV-cache growth is invisible in runtime allocations on both engines** — pools are
  pre-allocated (zero `cuMemAlloc` under 16×512-token C=16 loads on either). The visible
  cross-engine contrast is startup *shape*: 3 monolithic allocs totaling 1.09 GiB
  (llama.cpp) vs 126 allocs totaling 7.72 GiB with per-layer clusters (vLLM).
- 4.4 (induced memory pressure) deliberately not run on the author's desktop-session box.

## Cross-cutting

- **Probe overhead ≈ 0 everywhere measured**: +0.0% ITL attached-vs-control at up to
  190k uprobe events/s (and ~55k/s on vLLM) — the launch/submitter threads have enough
  futex slack (Layer 2) to absorb per-event uprobe cost. The one observed distortion was
  self-inflicted (double probe attachment: wall ~7×, per-request metrics flat). Both
  `overhead.md` reports carry the Craun et al. co-located-cost caveat: our deltas bound
  user-visible cost, not total system cost.
- **Tooling lessons** (recorded in probe headers and the log): globbing bare `libcuda.so*`
  triple-counts via symlink aliases; `lhist()` with inline map arithmetic silently records
  nothing; empty positional args misalign bpftrace argument indexing; `wait` on a
  non-child pid returns instantly (poll for exit before parsing); vLLM's CUDA threads are
  named `python3`/`pt_*` so tgid filtering is required; the container driver is a host
  bind-mount, so host-path uprobes fire fine for container processes; `bpftrace --dry-run`
  is a fast traffic-free compile check.

## Reproducibility map

| What | Where |
|---|---|
| Phase 1 sweep + H2 validation | `results/phase1/20261010T122431Z/` + `tools/ttft.bt`, `phase1-sweep.sh`, `phase1-validate.py` |
| Phase 2 thread/sync censuses | `results/phase2/20261010T123353Z/` + `tools/phase2-threads.bt` |
| Phase 3 launch/alloc censuses | `results/phase3/20261010T192359Z/` + `tools/phase3-launches.bt`, `phase3-allocs.bt` |
| Phase 4 fault/mmap censuses | `results/phase4/20261010T201207Z/` + `tools/phase4-faults.bt` |
| vLLM cross-engine runs | `results/vllm/20261010T202713Z/` (relaunch command in its `meta.txt`) |
| Servers | llama.cpp: `~/code/llama.cpp/build/` (graphs ON) and `build-off/` (OFF), flags in the 2026-10-09 results-log entry; vLLM via the local `vllm/vllm-openai` image + the sibling repo's `hf-cache` volume |
| Ground truth | `tools/loadgen.py` — `--api llamacpp|openai`, `--gap` arrival pacing, `ttfb_s`/`end_s` fields |

## Open threads (author calls)

- H1 re-test on hardware where kernel durations dominate submission (slower GPU, larger
  model) — the one scope where its prediction could still separate.
- H2's network-layer validation was never run against vLLM (the validator is
  engine-agnostic; `ttft.bt` was only ever attached to llama.cpp).
- H4's bursty-vs-continuous leg on vLLM (confirmed on llama.cpp only).
- Phase 5 (multi-tenant / per-tenant attribution) — optional stretch in the design.
- Symbol-rich llama.cpp rebuild for user-mode stack attribution (would change the binary
  under observation — explicitly an author decision, not made).
- 4.4 memory-pressure induction — deferred while the box hosts a desktop session.
