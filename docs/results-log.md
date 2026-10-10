# Results log

Chronological log of experiments, observations, bugs and surprises. **Newest entries at the
top.**

Every entry records: date · phase · what was run · what was observed · what it means for the
hypotheses in `hypotheses.md`.

Rules:

- Log **failures and surprises**, not just successes. They are the most useful entries.
- Record the **exact kernel, driver and server versions** with any measurement. A number without
  its environment is not reproducible.
- State whether page caches were dropped, and whether CUDA graphs were on.
- If a hypothesis is refuted, say so plainly and update the status table in `hypotheses.md`.

---

## 2026-10-10 — vLLM introduced (cross-engine): H1 refutation holds cross-engine; graphs contrast 9× vs 566×; both engines hide KV at the API

- **Context:** vLLM 0.23.0 (`vllm/vllm-openai` image, digest sha256:97492385…, already
  local from the sibling repo), Qwen2.5-1.5B-Instruct bf16, pinned revision, weights from
  the sibling's `hf-cache` docker volume (2.9 GB cached — zero downloads; `HF_HUB_OFFLINE=1`).
  Own container `vllm-obs` on port 8081, `--gpu-memory-utilization 0.45`, coexisting with
  the idle llama-server (its idle-CUDA-zero behavior, Phase 3 Run 0, makes coexistence a
  measured non-confound). llama-server kept serving on 8080 throughout. Artifacts:
  `results/vllm/20261010T202713Z/`.
- **Tooling lessons (all in probe headers):** vLLM's CUDA threads are named
  `python3`/`pt_*`, not the process title — comm filters catch nothing; the probes now
  take `<tgid> <comm>` and MUST be given two non-empty args (an empty positional arg
  misaligns bpftrace's arg indexing). Also: `wait` on a non-child pid returns instantly,
  so kill-and-wait must poll for exit before parsing (a probe's exit-map dump races a
  naive parser). The container's libcuda is the host driver bind-mounted — same inode, so
  uprobes on the host path fire fine for container processes.
- **V2 — H3's eager half (cold caches, fault census):** EngineCore fault census:
  **498,152 user faults — 94.6% during startup** (weights eagerly read: 162,161 mmap
  calls, zero madvise); post-startup RSS 2.26 GB. First request: 26,916 faults in 0.77 s
  (+114 MB RSS, peak 8,520/10ms) — **steady state: 7 faults total**. So the eager engine
  concentrates weight faults at load as predicted, but "first inference looks like steady
  state" is false in the strict fault sense — the first-request residue is engine
  machinery (lazy CUDA/sampling/detok pages), not weights. Together with Phase 4: **the
  mmap-vs-eager fault distinction has collapsed across modern engines** — llama.cpp
  prefaults by design and vLLM loads eagerly, and both put ~90–95% of faults at load.
- **V3 — H1's second chance, on vLLM:**
  - Graphs instance (default): **CUDA graphs are captured at STARTUP, not first
    inference** (captures/instantiations = 0 in every serving window) — the opposite of
    llama.cpp's first-request capture; explains the 27k first-request fault burst's
    content being machinery. Decode step ≈ **1 graph replay + ~34 eager launches**
    (sampling/KV ops live outside the graph); ~9 HtoD, ~3 DtoD and ~1 DtoH memcpy per
    step.
  - Eager instance (`--enforce-eager`): prefill = **656 launches per 600-token forward**
    (~26 ms); decode = **~321 launches per step** (5.8 ms ITL). **The inter-arrival
    distributions still overlap: prefill 81.1% < 20 µs vs decode 77.2% < 20 µs, medians
    in the same bucket** — submission is CPU-bound in both phases even with ms-scale
    prefill kernels; the stream queue absorbs them. **H1's falsification criterion 1
    fires on the second engine too: the refutation is now cross-engine** (llama.cpp
    graphs-off and vLLM eager, same GPU). What IS separable is step *structure* —
    656-vs-321 kernels per step, 26-vs-5.8 ms steps, 25k-vs-55k launches/s phase rates —
    but that is request-context knowledge, not the inter-arrival distribution H1
    specified.
  - **3.4 graphs on/off, cross-engine:** vLLM collapses the API surface ~9×
    (321 → 35 calls/step) with **~0% ITL benefit** (5.8 → 5.9 ms — bf16 kernels are too
    big for launch overhead to matter); llama.cpp collapses it 566× with ~12% ITL
    benefit (tiny Q4 kernels are launch-bound). Same mechanism, opposite economics —
    a clean externally-visible instance of "why CUDA graphs help small-kernel models."
- **V4 — KV-cache visibility (4.3), cross-engine:** ZERO `cuMemAlloc*` during a
  16×512-token C=16 run on vLLM (VRAM static, RSS +4 MB) — PagedAttention blocks come
  from the startup pool, invisible at the API boundary, exactly like llama.cpp's static
  pools. The visible cross-engine contrast is at **startup**: llama.cpp = 3 monolithic
  allocs totaling 1.09 GiB; vLLM = **126 allocs totaling 7.72 GiB** with per-layer
  clusters (28 × ~125 MiB, 28 × ~55 MiB, 50 × ~25 MiB). The design's "KV growth visible
  in allocation patterns" is negative at the API level for both engines on this host;
  block-table growth is host-side metadata.
- **Probe cost:** ITL +0.0% attached vs control on vLLM eager (~55k uprobe events/s) —
  the futex-slack/submitter-slack pattern holds on the second engine.
- **Verdicts:** H1 refuted (cross-engine, recorded); H3's eager half measured (vLLM:
  load-concentrated as predicted, first-request machinery residue noted); both engines
  hide KV behind pre-allocated pools. **The experimental arc is complete: Phases 1–4
  plus the vLLM cross-engine leg.** vLLM container stopped and removed after the runs
  (image + volume remain; relaunch command in the artifacts' meta.txt). Next: Phase 5
  (multi-tenant attribution, optional) or consolidation/write-up of the results arc.

## 2026-10-10 — Phase 4 (memory system): faults at LOAD, not first inference — H3 refuted; llama.cpp prefaults by design

- **Context:** server binary build-off/ (graphs OFF) for all runs; two serving configs:
  `-ngl 99` (GPU, weights in VRAM) and `-ngl 0` (CPU, weights in file pages). Kernel
  7.0.0-38-generic, bpftrace 0.25.0, THP `[madvise]` mode. **Page caches dropped**
  (`sync` + `echo 3 > /proc/sys/vm/drop_caches`, MemFree ≈ 26 GB verified) before each
  cold run — the pre-registered H3 confound control, executed. No other tenant touched
  the model file. Probe: `tools/phase4-faults.bt` (exceptions:page_fault_user +
  mmap/madvise census, in-kernel; attach before server start). Artifacts:
  `results/phase4/20261010T201207Z/`.
- **4.1 — load-time tracing:** the model is ONE lazy-looking mapping — mmap census shows
  a single 1,050–1,075 MiB call among ~220 smaller mmaps — but the mapping is prefaulted
  by design: GPU config shows `madvise(WILLNEED)` ×1 then `madvise(DONTNEED)` ×1 (plus
  46× advice 102 — MADV_GUARD_INSTALL on ≥6.12 kernels [INFERENCE]); CPU config shows
  ZERO madvise calls and the faulting happens inside `mmap(2)` itself via **MAP_POPULATE**
  (`src/llama-mmap.cpp:493`, called as `init_mappings(true, …)` from
  `src/llama-model.cpp:1840` — prefetch hardcoded ON).
- **4.2 — the H3 test (fault storms, cold caches, first request vs steady):**

  | config | total user faults | at load | first request | steady state | post-load RSS / RssFile |
  |---|---|---|---|---|---|
  | `-ngl 99` (GPU) | 54,676 | **49,189 (90%)** | 3,328 in 0.25 s (13.3k/s) | ≈2.6k/s | 550 MB / 270 MB |
  | `-ngl 0` (CPU) | 72,138 | **70,526 (98%)** | 1,284 in 0.58 s (2.2k/s) | ≈0.2k/s | 1.58 GB / 1.22 GB |

  - The GPU path is prefault → upload → **MADV_DONTNEED the file pages** (RssFile collapses
    to 270 MB after the VRAM upload); the CPU path keeps all 1.22 GB resident.
  - First inference is NOT a fault storm in either config: the residual excess vs steady
    state (~5× rate, a few thousand faults, +19.5 MB RSS on the GPU config) is
    tokenizer/sampling/buffer pages — an order of magnitude short of weight faulting, and
    decaying into steady state.
  - **H3 is REFUTED per its own falsification criterion 2: "faults appear at load time
    for the mmap-based server too, meaning loading is not actually lazy and the warmup
    explanation is wrong."** Exactly what happened, in BOTH serving configurations: the
    classic first-inference-fault-storm story (true of older llama.cpp lore) has been
    engineered away upstream — prefetch is hardcoded on. The warmup request's real cost
    on this build is the CUDA-graph capture (Phase 3) plus this small non-weights residue.
  - The prediction's eager-loading half (PyTorch/vLLM faults at load) remains untested —
    that comparison waits for the vLLM introduction.
- **4.3 — KV-cache growth:** ZERO `cuMemAlloc*` calls during a C=16, 16×512-token run;
  VRAM static (1836→1838 MB); host RSS +144 MB = per-connection state, not KV. llama.cpp
  pre-allocates pools at startup (Phase 3.3's three cudaMallocs), so **KV growth is
  invisible in the allocation stream** — the design's "allocation and RSS patterns"
  expectation holds for block-allocating engines (vLLM PagedAttention), not this server.
- **4.4 — under pressure: NOT RUN.** Inducing host memory pressure deliberately on the
  author's desktop-session box was not worth the risk without asking; deferred (the box
  is not headless). H3's verdict does not depend on it.
- **Verdict recorded:** H3 refuted (llama.cpp 0.6.0-dev, both configs; mechanism named
  from source). **All four hypotheses now have verdicts: H1 refuted, H2 confirmed,
  H3 refuted, H4 confirmed.** What survives across the project so far is the external
  observability itself — network-derived TTFB/ITL (H2), the futex-metronome CPU side,
  the graph-collapse signature, step cadence and batch amortization (H4) — while the
  two "asymmetry" hypotheses (prefill/decode gaps, fault timing) failed because llama.cpp
  0.6.0-dev has already engineered away the asymmetries H1/H3 expected to observe.
  **Next:** vLLM introduction (serves H3's eager-loading comparison, H1's graphs test via
  `--enforce-eager`, and the design's continuous-batching comparisons) or Phase 5
  (multi-tenant/attribution, optional).

## 2026-10-10 — Phase 3 (CUDA uprobes): llama.cpp ships CUDA graphs; H1 refuted at the API level, H4 confirmed

- **Context:** same host; kernel 7.0.0-38-generic, driver 595.99.02 (CUDA 13.2), bpftrace 0.25.0.
  Two llama.cpp binaries this phase, both 0.6.0-dev (clang 21.1.8): `build/` = CUDA graphs
  ON (the shipping configuration), `build-off/` = `GGML_CUDA_GRAPHS=OFF` (author-approved
  2026-10-10; 2.2 min build; flag verified in CMakeCache). Server swapped to build-off
  mid-phase (pid 56122); `meta.txt` records the swap and which binary each run measured.
  Page caches not dropped. Artifacts: `results/phase3/20261010T192359Z/`.
- **GPU-tenant surprise:** Phase 3 opened blocked — llama.cpp OOM'd at model load because
  the sibling repo's `l0-vllm` container (full-stack-serving; vllm-openai:v0.23.0,
  `--gpu-memory-utilization 0.90`) held 14.6 of 16 GiB. It was idle (no established
  connections) and was stopped via that repo's own `make down` target (restore:
  `make -C ~/code/full-stack-serving up`). The design's "other tenants" confound is real
  on this box; Phase 3 meta records `gpu_tenants: none` for all runs.
- **Probes:** `tools/phase3-launches.bt` (cuLaunchKernel*/cuGraphLaunch*/capture/memcpy
  census, 96 uprobes on the stable soname `libcuda.so.1`) and `tools/phase3-allocs.bt`
  (cuMemAlloc*/Free census). Two tooling lessons, logged in the probe headers: (1) globbing
  bare `libcuda.so*` matches the `.so`/`.so.1`/`.so.595.99.02` aliases as separate paths —
  bpftrace attaches per path and TRIPLE-counts every event (Run D's raw file `d-probe.txt`
  has 3× counts; every later run is clean); (2) `lhist()` with an inline map-lookup
  expression silently records nothing — compute into a `$d` variable first.
- **FINDING — llama.cpp 0.6.0-dev runs CUDA graphs by default.** `GGML_CUDA_GRAPHS`
  defaults ON; there is no runtime switch. Externally visible without any server
  knowledge (graphs-ON binary): an idle server makes ZERO CUDA API calls (15 s baseline);
  the first inference of each new shape produces ONE capture event (cuStreamEndCapture=1,
  cuGraphInstantiate=1) containing a 1,132-launch cuLaunchKernel burst (all gaps < 20 µs
  — the whole kernel DAG submitted once); every subsequent step is ONE cuGraphLaunch with
  ZERO cuGraphExecKernelNodeSetParams — kernel arguments are baked at capture, per-step
  state lives in device memory. Identical-shape steps replay: 16 × 600-token prompts →
  1 prefill capture + 15 replays; the 8×64 run shows 511 replays = 504 decode steps + 7
  prefill replays + 1 capture-run — the arithmetic closes exactly.
- **H1 test (graphs OFF — the pre-registered confound control), prefill vs decode at C=1:**
  - decode: 579,584 cuLaunchKernel calls / 1,024 steps = **566 launches per step**
    (~130 k/s), 99.86% of inter-launch gaps < 20 µs; step cadence visible as 360 gaps in
    the 2–52 ms bucket.
  - prefill (600-token prompt, max_tokens 2): 18,112 / 16 requests = **1,132 per request =
    exactly 2 steps × 566** — the same burst size and rhythm as decode; a 600-token
    prefill lasts ~4 ms on this GPU (TTFT p50 4.5 ms), comparable to a 3.2 ms decode step.
  - **H1 is REFUTED per its own falsification criterion 1:** the prefill and decode
    inter-arrival distributions overlap almost completely (both ~99.9% < 20 µs; medians in
    the same bucket; the between-phase difference is smaller than the within-phase
    spread). The prefill/decode asymmetry lives in kernel SIZE and DURATION, which the
    CUDA API boundary does not expose — and on this GPU even the phase-duration asymmetry
    has collapsed (prefill ≈ decode-step time). Scope note, recorded plainly: the criteria
    were pre-registered for this server+GPU; a slower GPU or larger model may re-separate
    the phases. As registered, H1 fails.
  - Constructive residue (logged as findings, not a rescue): the graph DAG size
    (566 kernels/step), the step cadence, and batching's launch amortization are all
    cleanly visible from outside (below).
- **H4 test (launch gaps, bursty vs continuous, graphs OFF): CONFIRMED.**
  - Bursty single-stream (8 requests, 1 s arrival spacing): launches occupy only **23% of
    10 ms buckets**; 7 idle windows appear in the inter-launch gap distribution at
    752–802 ms — the 1 s arrival spacing minus the ~230 ms request duration. Idle gaps
    track arrivals.
  - Continuous (C=16, 256 req, 64 tok): **100% of buckets active, zero idle windows**;
    3,959 step-cadence gaps at 2–52 ms (ITL 4.2 ms).
  - Falsification checks: gaps change measurably between regimes (23% → 0% idle); they
    track arrival spacing and step cadence, not any fixed tick; llama.cpp's 4-slot
    batching IS continuous, and the bursty single-stream case is the no-batch baseline in
    the sense H4 needs (nothing exists to batch between arrivals). Prediction met on both
    sides.
- **Batching's launch signature (Phase 3.1's core fact, externally):** 566 launches per
  1-slot decode step → ~800 per 4-slot batched step → **566 launches/token at C=1
  amortizes to ~204/token at C=16 (2.8×)**. Continuous batching's efficiency is visible in
  API-level launch counts alone.
- **3.4 graphs ON vs OFF (measured):** API launches per decode step: 1 (cuGraphLaunch)
  vs 566 (cuLaunchKernel) — **566× collapse**. ITL at C=1: 2.8 ms (ON) vs 3.2 ms (OFF),
  ~12% faster with graphs; at C=16: 3.7 ms (ON, Phase 2 cross-session) vs 4.2 ms (OFF).
  CUDA graphs both collapse the observable launch stream and speed small-batch decode —
  exactly the confound the design flagged for H1, now quantified on llama.cpp.
- **3.2 memcpy census (consistent across both binaries):** ~9 cuMemcpyHtoDAsync_v2 + ~1.9
  cuMemcpyDtoHAsync_v2 per step; sampling/logit traffic rides outside any graph.
- **3.3 allocation watch (graphs-OFF server startup):** 3 cuMemAlloc_v2 calls totaling
  1,166,241,408 B (~1.09 GiB): 934.7 MiB (model weights — matching the OOM message from
  the vLLM-contention incident), ~118 MiB and ~68 MiB (compute/KV buffers); 1 cuMemFree
  during startup. The "KV cache grabs its headroom" moment is visible as discrete
  cudaMallocs from outside.
- **Probe overhead:** at 130–190k uprobe events/s, attached vs control: ITL +0.0% in
  every run; TTFT +2.1% / −2.2% (run noise). The launch-submitting thread has enough
  futex slack (Phase 2's finding) to absorb the per-launch uprobe cost entirely.
- **Verdicts recorded:** H1 refuted (API level, this configuration); H4 confirmed.
  **Next:** Phase 4 (memory/page faults — H3, the last untested hypothesis) or vLLM
  introduction (the graphs comparison now has its llama.cpp leg; vLLM brings
  `--enforce-eager`).

## 2026-10-10 — Phase 2 (scheduler/CPU side): llama.cpp is a futex metronome; GPU waits invisible; BCC casualties on kernel 7.0

- **Context:** server unchanged (pid 8945, flags as 2026-10-09; page caches not dropped;
  CUDA graphs N/A). Load for every experiment: loadgen C=16, 256 requests, 64 tokens
  (~16 s sustained decode, ~1k tokens/s, ITL mean 3.7 ms, TTFT p50 ~0.74 s), plus a
  detached control. Artifacts: `results/phase2/20261010T123353Z/`.
- **bcc-tools authorization resolved as "already present":** `bpfcc-tools` 0.35.0 was
  installed all along; the inventory's check only looked at `/usr/share/bcc/tools`
  (upstream layout) and missed Ubuntu's `/usr/sbin/*-bpfcc` packaging (132 tools).
  Detection fixed in `tools/host-inventory.sh`; `docs/environment.md` corrected.
- **BCC on kernel 7.0.0-38 — two findings:**
  1. `profile-bpfcc` does not compile: kernel-7.0 `include/linux/fs.h` static_assert on
     `sizeof(struct filename)` defeats BCC 0.35's embedded clang. Substituted: bpftrace
     `profile:hz:49`.
  2. `offcputime-bpfcc` attaches and traces, but its report generation hangs
     indefinitely post-SIGINT at Phase-2 event volumes (three attempts: full stacks,
     `-K`, `-K` + 4 s load — all reports lost; idle-scale traces print instantly, as
     in the Phase 1 session's first smoke). Substituted: bpftrace block-site census.
     The design's Phase 2.2 assumption (BCC offcputime delivers the off-CPU report)
     does not hold on this box; CO-RE escalation for off-CPU attribution is now
     scheduled earlier than the design assumed.
- **E1 — thread census** (`tools/phase2-threads.bt`, in-kernel aggregation): 47 threads,
  45 with comm `llama-server` (NVIDIA's two `cuda-*` helpers excluded by the filter).
  During decode ~15 threads run hot (11–13k voluntary context switches each in 16 s,
  wchan `futex_do_wait` at rest). **Every on-cpu run slice < 250 µs** (370k slices).
  Off-cpu episodes: 388k under 10 ms plus a thin tail at ~110–265 ms and ~510 ms
  (≈ queue-wave scale, matching Phase 1's TTFB staircase). Wakeups of server threads:
  388k — **99.97% issued from `swapper/*`, i.e. interrupt/timer context; 122 from the
  server itself.**
- **E3 — sync census:** ~1.03M futex syscalls in the probe window (~60 per token
  generated if concentrated in the load window): FUTEX_WAKE 470k, FUTEX_WAIT 287k,
  FUTEX_WAIT_BITSET 277k. Against that, only **122 wakeups were ever delivered from
  server context — almost every explicit FUTEX_WAKE found an empty wait queue**
  (waiters released by timer expiry first; hrtimer-sleeper frames inside the futex
  stacks confirm the waits are timed). glibc `pthread_cond_wait` (uprobe): 27.6k calls
  ≈ 1.7 per token, ~40x below the raw futex traffic, so most futex use is NOT
  std::condition_variable (unattributable without server symbols; open question).
- **E2c — where server threads sleep (396,669 blocks counted):** **99.6% in
  `futex_do_wait`**, 0.2% poll-with-hrtimer-timeout (idle epoll handlers), single-digit
  counts for network/tty frames, and **zero NVIDIA-driver wait sites** — during
  sustained GPU decode, threads never sleep inside the driver. The naive "worker
  threads wait on the GPU" picture is refuted: the CPU side of this llama.cpp build is
  a futex metronome.
- **E4 — on-cpu profile (49 Hz, 3,416 samples):** **97.4% user-mode** (empty kernel
  stack), ~2% futex syscalls, <1% socket I/O + minor faults. User-mode attribution is
  impossible on this binary: llama-server ships stripped (8 text symbols, 12 KB symtab,
  no `.debug_*`), so design §Phase 2.3 ("uprobes on server functions") cannot run;
  glibc uprobes were used instead. A symbol-rich rebuild would change the binary under
  observation — author's call, not made.
- **Deliverable — where orchestration overhead actually lives:** per token generated,
  the server executes ~60 futex syscalls and ~24 sleep/wake cycles, essentially all in
  its own threadpool synchronization, with most wake syscalls landing on empty queues;
  GPU/driver waits are absent from the sleep census. At this load the churn is
  invisible in user-visible latency (ITL flat 3.7 ms across all runs).
- **Probe overhead (`overhead.md`):** every single-probe run within ±1.5% of control
  on ttft p50, ITL flat. One operator error kept as data: two concurrent offcputime
  instances → wall ≈7x (~110 s) with per-request metrics unchanged — the cost
  appeared between requests, not within them.
- **Next:** Phase 3 (CUDA uprobes; `cuLaunchKernel`/`cuMemAlloc*` resolvable per
  inventory §4) or Phase 4 (memory/page faults).

## 2026-10-10 — H2 verdict: confirmed (author ruling — same-interval TTFB)

- The author ruled that the pre-registered "probe TTFB vs client TTFB" comparison is
  **same-interval**: the client's origin is the probe's own `r0` (both clocks join), the
  reading consistent with the framing-symmetric ITL definition approved before the sweep.
  Conventional write-origin TTFB stays logged as informational.
- Final validated numbers (`tools/phase1-validate.py` updated to compute the ruling; same
  collected sweep data, `results/phase1/20261010T122431Z/`):
  - **TTFB same-interval:** median abs rel err 5.9% / 4.9% / 0.0% / 0.0% at C=1/4/16/64,
    Pearson r 0.956 / 0.910 / 1.000 / 1.000.
  - **ITL framing-symmetric:** median abs rel err 0.1% at every C, r ≥ 0.9987.
  - Conventional write-origin TTFB (informational): 10.1% / 6.1% / 0.0% / 0.0%.
  - Both criteria (median ≤ 10%, r ≥ 0.9) met at every concurrency. **H2 confirmed.**
- `docs/hypotheses.md` carries the ruling as a dated addendum; the falsification criteria
  text is unchanged.
- Next: Phase 2 (scheduler/CPU; needs `bcc-tools` — install is an author call) or Phase 3
  (CUDA uprobes; GPU ready).

## 2026-10-10 — Phase 1 sweep (H2 measured): ITL confirmed at every C; TTFB pending one adjudication

- **Context:** same host/session as the entries below (kernel 7.0.0-38-generic, driver
  595.99.02, llama.cpp 0.6.0-dev CUDA build, bpftrace 0.25.0, server pid 8945, page caches
  NOT dropped, CUDA graphs N/A for llama.cpp). H2 was finalized (TTFB clause + 10%/0.9
  corrected tolerances) BEFORE this run; loadgen gained `ttfb_s`/`end_s`, the validator the
  framing-symmetric metrics. Sweep: `sudo ./tools/phase1-sweep.sh` →
  `results/phase1/20261010T122431Z/` — per C (1, 4, 16, 64; requests 16/16/64/256,
  max_tokens 32): unmeasured warmup, detached baseline, attached run, SIGINT for exit
  histograms. `meta.txt` records `git c0b78c4-dirty` — the TTFB tooling edits (committed
  immediately after this entry) were in the working tree during the run.
- **Two tooling bugs found and fixed mid-sweep (first attempts kept as artifacts):**
  1. The sweep's attach check grepped `Attaching`, but bpftrace 0.25 prints
     `Attached N probes` — first attempt aborted at c=1
     (`results/phase1/20261010T122415Z/`, kept). Check now matches `Attach(ed|ing)`.
  2. The validator's pairing scrambled the concurrent burst at C≥16: all client writes and
     all server first-reads land within microseconds, so greedy read-proximity matching
     misassigned records across queueing waves — visible as impossible pairs (client TTFB
     < probe TTFB on one connection). Fixed with a two-endpoint cross-check (r0↔write AND
     last↔end_s, 50 ms). Post-fix: 0 unmatched at all C, 0 impossible pairs at C=1/4/16;
     residual same-wave swaps ≈2/256 at C=64, bounded by the within-wave TTFB spread —
     medians unaffected.
- **Results (attached runs, post-fix validator; send/token ratio 1.125 at 32 tokens
  everywhere):**

  | C | TTFB med err (write-origin) | TTFB r | ITL-fs med err | ITL-fs r | raw TTFT med err | raw ITL med err |
  |---|---|---|---|---|---|---|
  | 1 | 10.1% | 0.926 | 0.1% | 0.9999 | 76.8% | 8.4% |
  | 4 | 6.1% | 0.910 | 0.1% | 0.9988 | 58.9% | 8.7% |
  | 16 | 0.0% | 1.000 | 0.1% | 0.9987 | 1.0% | 8.6% |
  | 64 | 0.0% | 0.999 | 0.1% | 0.9991 | 0.2% | 8.6% |

- **Probe overhead (`overhead.md`; quote no eBPF number without it):** ITL mean +0.0% at
  C=16 and C=64; ttft/ttfb p50 −7.5%/−16.7% at C=1 (sub-millisecond run-to-run noise — the
  attached run was *faster*), +2.5% at C=64. Tail deltas (ttft p90 −58.8% at C=4) are
  single-run tail noise. No probe cost distinguishable from noise at any C at these event
  rates (~9.2k sends at C=64).
- **Verdict per the finalized criteria:** ITL (framing-symmetric) — **confirmed at every
  C** (median 0.1%, r ≥ 0.9987; error flat across concurrency — the opposite of the
  degradation criterion). TTFB — confirmed at C=4/16/64. At C=1 the write-origin median is
  **10.1%**, exceeding the 10% bar by 0.1pp. Cause, diagnosed from the same data: the
  client's TTFB clock starts at its socket write, the probe's at the server's read — the
  write→read interval (p50 51 µs at C=1 here; 17–161 µs observed in the smokes) is inside
  the client's number and invisible to the probe; on ~1 ms idle-server TTFBs that fixed
  path cost is ~10%, and it vanishes as TTFB grows. Same-interval comparison (client
  origin moved to the probe's r0 — both clocks join), computed over the same collected
  data: **5.9% / 4.9% / 0.0% / 0.0%**, r 0.956 / 0.910 / 1.000 / 1.000 — under the bar at
  every C. Which variant is *the* metric is an author call; both are reported here so
  nothing is hidden.
- **Emergent findings (arguably worth more than the verdict):**
  - **Under load, the raw TTFT metric heals itself.** At C=1, probe (t0−r0) vs client
    first-token is hopeless (76.8%) — headers precede tokens. At C=16/64 it is 1.0%/0.2%:
    with slots contended the header flush waits for slot/task start and rides with the
    first token (mechanism [INFERENCE], consistent with the TTFB≈TTFT convergence), so
    first-token TTFT becomes externally recoverable exactly when it matters most.
  - **Queueing is externally visible as a TTFB staircase.** At C=16, TTFB quantizes at
    ~130 ms — one request-duration per queue wave. Across the sweep (attached runs), TTFT
    p50 goes 3.7 ms → 6.8 ms → 374 ms → 1.97 s (532×) while ITL mean goes 2.7 → 3.9 ms
    (+44%): Phase 1.4's claim (TTFT degrades, ITL stays flat) is measured, and the
    prefill/decode asymmetry is visible from `tcp_sendmsg` timing alone.
  - Framing is 4 extra sends per connection regardless of length (68/64 at 64 tokens,
    36/32 at 32) — the sends/tokens ratios 1.0625 and 1.125 are both fully explained.
- **Next:** author adjudicates the TTFB origin variant → H2 status final; then Phase 2
  (needs `bcc-tools`, an install = author call) or Phase 3 (CUDA uprobes).

## 2026-10-10 — Saturn session: inventory regenerated, ttft.bt first run — TTFT-definition finding

- **Context:** first probe session on saturn (Linux 7.0.0-38-generic, NVIDIA driver 595.99.02,
  llama.cpp 0.6.0-dev built with clang 21.1.8 + CUDA, bpftrace 0.25.0). Server restarted as a
  session service (pid 8945, flags unchanged from 2026-10-09: `127.0.0.1:8080 -ngl 99 -c 4096
  -np 4`, n_ctx_slot 1024, 4 slots; readiness regex `llama_server: listening` works). Page
  caches NOT dropped (weights resident; H3/Phase 4 owns cache state). CUDA graphs: N/A —
  llama.cpp decode path here, no graph capture involved.
- **Runbook 1 done — inventory regenerated:** `sudo ./tools/host-inventory.sh >
  inventory/report.md` (root run, collected 2026-10-10T12:07:04Z). The committed copy
  predated the toolchain install and the script fixes; now current.
- **Runbook 3 done — `ttft.bt` first-ever run** (C=1, 2 requests; repeated once after a probe
  warning fix). Attaches cleanly, one `CONN` line per closed connection, exit histograms on
  SIGINT. All four known risks from the tooling entry resolved:
  1. `tcp_sendmsg` / `tcp_recvmsg` / `tcp_close` all exist (485 `tcp_*` kprobes total).
  2. `delete()` prints "Return value discarded" in **both** spellings — cosmetic; original
     spelling kept, `ttft.bt` header comment corrected. The int64/uint64 sign-mix warning on
     `@bytes` was real noise and is fixed (`(int64)arg2` cast); re-smoked, identical `CONN`
     structure. `bpftrace --dry-run` is a fast traffic-free compile check.
  3. `tcp_close` fires in server context at C=1 — no orphaned map entries observed.
  4. `pid` filter matches the whole server process.
- **FINDING — probe TTFT (`t0 − r0`) measures the HTTP-header write, not the first token.**
  llama-server emits 3 pre-token framing writes and one trailing `[DONE]` write (inferred by
  count — nsends 68 = 3 + 64 tokens + 1; payload not read), so `t0` is a framing write that
  precedes the first token by a variable gap. First validator run against real probe output:
  pairing works (`abs_t0_s` ↔ `r0`, write→read deltas 0.02–0.16 ms, 4/4 pairs across both
  smokes):
  - **TTFT: median abs error 70%** (pairs: 96.3 / 70.5 / 18.3 / 69.5%). Definitional, not
    noise. The Mac fixture missed it — the dummy SSE server sends no headers before the
    first token.
  - **ITL: median abs error 4.3%** (pairs: 11.6 / 4.3 / 3.7 / 4.3%), Pearson r 1.0 in both
    smokes. The strong metric.
  - The mean-ITL framing bias is **mixed-sign**, replacing the fixture's clean −11% model:
    probe mean ITL = (header→token gap + token span + `[DONE]` gap) / (nsends−1), ground
    truth = token span / (ntokens−1). Observed −4.3% when the header gap is ~3–4 ms, +11.6%
    when it is ~35 ms; the sign flips around a ~12 ms header gap (64 tokens, ITL ≈ 2.9 ms).
    The validator's per-pair sends/tokens ratio (1.062) keeps the framing visible.
- **Surprise:** the header write itself is not prompt. `t0 − r0` was 1.34–1.41 ms on three
  connections but **19.8 ms** on the first request after a few minutes idle (that request's
  client TTFT was 24.2 ms, so the header still preceded the first token by only ~4 ms — the
  TTFT error for that pair was coincidentally small, 18.3%). Mechanism unknown (slot
  scheduling? GPU wake?); recorded as-is. Consequence: **no constant correction can turn
  `t0 − r0` into first-token TTFT** — the gap is state-dependent (observed 3–35 ms).
- **Consequence for H2 (blocks the sweep — author decision, runbook step 2):** as currently
  worded, H2's TTFT clause would be refuted by llama.cpp's SSE framing, not by timing
  quality. Options: (a) redefine the clause as time-to-first-byte, add `ttfb_s` to loadgen
  ground truth, probe unchanged; (b) extend the `CONN` line with early-send timestamps
  (t1..t3, last-but-one) so the validator locates the token boundary under the checkable
  per-connection assumption `nsends == n_tokens + 4`; (c) keep as-is and record the limit of
  external observation. Tolerances in `hypotheses.md` remain placeholders until this is
  decided.
- **State left behind:** server running (pid 8945). Next: author decision →
  `sudo ./tools/phase1-sweep.sh` → validate → set H2 status.

## 2026-10-10 — Phase 1 tooling authored on the Mac (no probe work possible here)

- **Context:** bpftrace does not exist on macOS, so nothing below has run against a real
  kernel yet. Everything is authored and checked as far as the Mac allows (`bash -n`,
  `py_compile`, and an end-to-end fixture test of the validator against a dummy SSE server);
  the first smoke happens on saturn.
- **`tools/ttft.bt` — the Phase 1 probe (tests H2).** kprobes on `tcp_recvmsg`,
  `tcp_sendmsg`, `tcp_close`, filtered to the server pid (`$1`). Per-connection state lives
  in maps keyed by `struct sock*`: `@r0` (first read ≈ request arrival), `@t0` (first send),
  `@last`, `@nsends`, `@bytes`. Aggregation stays in-kernel — TTFT/ITL feed `lhist` maps
  (bpftrace 0.25: `lhist(v, min, max, step)`); user space receives **one `CONN` line per
  closed connection** (request granularity, not token granularity) plus the exit
  histograms. Positional args (`arg0`/`arg2`) instead of BTF param names, for
  portability. TTFT_probe = t0 − r0; mean ITL_probe = (last − t0)/(nsends − 1).
- **`tools/phase1-sweep.sh` — attached-vs-detached sweep.** Per concurrency C (default
  1 4 16 64): warmup (unmeasured) → detached baseline → probe attached → identical run →
  SIGINT (exit histograms). Writes `results/phase1/<UTC>/`: `meta.txt` (kernel, driver,
  server cmdline, bpftrace version, git describe, caches-not-dropped statement), per-C
  `detached.jsonl` / `attached.jsonl` / `probe.txt`, and `overhead.md`. Refuses to run
  without root or a live `llama-server`, and verifies the probe actually attached before
  the attached run.
- **`tools/phase1-validate.py` — H2 metrics + probe cost.** `--mode match` pairs each
  ground-truth record with the `CONN` line whose first-read time is nearest the client's
  write time, then reports per-pair TTFT/ITL, median absolute relative error, Pearson r,
  and the send/token ratio. `--mode overhead` prints the attached-vs-detached summary
  delta per concurrency. The H2 tolerances it echoes (10% / 0.9) are placeholders —
  **the author finalizes them before the first measurement.**
- **`tools/loadgen.py` gained one field: `abs_t0_s`** — the absolute CLOCK_MONOTONIC origin
  of each request's timestamps. bpftrace `nsecs` and asyncio `loop.time()` are both
  CLOCK_MONOTONIC-since-boot on the same host, so client records and kernel `CONN` lines
  join without clock conversion. Verified live against a dummy SSE server on the Mac
  (4/4 ok, 32 tokens/request, `abs_t0_s` in the monotonic domain).
- **Send/token bias (affects the H2 error budget):** the Phase 0a smoke saw 288 server
  sends vs 256 client tokens (≈1.125 sends/token — SSE framing, `data: [DONE]`, headers).
  Per-connection *mean* inter-send time is therefore biased low by roughly
  (n_tokens−1)/(n_sends−1) ≈ 0.89 before any measurement error. The validator reports the
  per-pair ratio so this stays visible; the fixture test reproduces it exactly (11.4% ITL
  error at ratio 1.125, TTFT error 2.0%).
- **Date-label convention fixed:** log dates are the authoring Mac's local time (UTC−5).
  The former 2026-10-10 labels were UTC artifacts (inventory collected
  2026-10-10T03:26:40Z = 2026-10-09 22:26 local, matching git) and broke this file's
  newest-first order. Relabeled here and in `docs/environment.md`; `inventory/report.md`
  keeps its explicit UTC timestamp.
- **Known risks for the first smoke (saturn):** (1) positional args assume
  `tcp_sendmsg(sk, msg, size)` / `tcp_recvmsg(sk, …)` / `tcp_close(sk, …)` — verify with
  `sudo bpftrace -l 'kprobe:tcp_*'`; `tcp_close` was not in the inventory's kallsyms
  check. (2) `delete(@m[k])` may print a deprecation warning on 0.25 — cosmetic;
  `delete(@m, k)` is the newer spelling if it ever hard-errors. (3) `CONN` lines only
  print when `tcp_close` runs in server context — RST-orphaned sockets would leak map
  entries (harmless within a run; fresh bpftrace per sweep step). (4) `pid` builtin is
  tgid — all server threads match, intended.

### Saturn runbook (supersedes the list in the 2026-10-09 entry below)

1. **Regenerate the committed inventory** — still pending; it predates the toolchain
   install and the script fixes:
   `sudo ./tools/host-inventory.sh > inventory/report.md`, commit, push.
2. **Author decision (blocks measurement):** finalize H2 tolerances in
   `docs/hypotheses.md` — 10% median-error / 0.9 correlation are flagged placeholders;
   decide with the send/token bias above in view.
3. **Smoke the probe** (first-ever run of `ttft.bt`): verify symbols, then
   `sudo bpftrace tools/ttft.bt $(pgrep -x llama-server) | tee /tmp/probe-smoke.txt`
   alongside one `tools/loadgen.py --concurrency 1 --requests 2 --out /tmp/smoke.jsonl`;
   expect ~2 `CONN` lines + exit histograms.
4. **Run the sweep:** `sudo ./tools/phase1-sweep.sh` (defaults 1 4 16 64; requests per C
   default max(16, 4C)). Outputs are root-owned.
5. **Validate:** per C, `python3 tools/phase1-validate.py --mode match --probe
   results/phase1/<ts>/c$C/probe.txt --ground-truth .../c$C/attached.jsonl`; read
   `overhead.md` — **quote no eBPF-derived number without it**.
6. **Log + decide:** results entry here (newest first; kernel/driver/server versions,
   caches-not-dropped statement, CUDA-graphs state), set H2 status in
   `docs/hypotheses.md`, commit `results/`.

- **Deliberately not done (author calls):** `bcc-tools` install (Phase 2), vLLM
  introduction, any change to the server binary or its flags.

## 2026-10-09 — Doc/code cleanup from Mac (no probe work)

- **Reviewed the repo from the authoring Mac** (cannot run probes here). Fixed four
  inconsistencies found during review:
  1. **AGENTS.md status was stale** — still said "Phase 0a, no experiments run" with the
     inventory as the immediate task, contradicting README and this log. Updated to reflect
     current state: inventory done, blockers cleared, server up, probe-attach goal met, next
     milestone is Phase 1.
  2. **`tools/host-inventory.sh` lockdown verdict was self-contradictory** — the `case` matched
     the literal `[none]` against raw `/sys/kernel/security/lockdown` output like
     `none [integrity] confidentiality`, so it never matched and fell through to "kprobes may be
     blocked" as a hard claim, contradicting the empirical proof that kprobes fire. Now parses
     the bracketed (active) token; the verdict for `integrity`/`confidentiality` says "may be
     blocked (verify empirically)" — honest, consistent with the empirical-first ethos.
  3. **`tools/host-inventory.sh` virtualisation row had a newline** — `systemd-detect-virt`
     outputs `none` but exits non-zero, so `|| echo 'not systemd / unknown'` appended, breaking
     the markdown table cell. Fixed to capture first, fallback on empty.
  4. **`tools/loadgen.py` used deprecated `asyncio.get_event_loop()`** (5 sites) — replaced
     with `asyncio.get_running_loop()`. No behavior change; removes the deprecation warning on
     the host's Python 3.14.4.
- **Flagged in `docs/environment.md`:** `bcc-tools` is still absent (0 tools), blocking
  Phase 2's `offcputime`/`profile` BCC tools. Install before Phase 2.

### Next steps on saturn

Superseded by the runbook in the 2026-10-10 entry above. Still-pending author actions:
regenerate `inventory/report.md` (the committed copy predates the toolchain install and
the script fixes) and finalize the H2 tolerances in `docs/hypotheses.md`.

## 2026-10-09 — Host inventory + first probe attach (Phase 0a start)

- **Ran `tools/host-inventory.sh` on the target host (saturn, Linux 7.0.0-38-generic,
  Ubuntu 26.04.1 LTS, x86_64).** Report committed to `inventory/report.md` and pushed.
  Read-only; nothing modified.
- **Hard blockers cleared:**
  - BTF present (`/sys/kernel/btf/vmlinux`, 7.1 MB) → bpftrace / CO-RE work.
  - Secure boot is enabled but **lockdown is not blocking**: a `kprobe:tcp_sendmsg` and
    `tracepoint:sched:sched_switch` bpftrace one-liner both attached and fired (7 `tcp_sendmsg`
    hits during `curl https://huggingface.co`, 1409 `sched_switch`). Root, so
    `perf_event_paranoid=4` / `unprivileged_bpf_disabled=2` do not matter.
  - Tracepoints: 32 sched, 18 net, 16 kmem — Phases 1, 2, 4 are sized.
- **Phase 3 viable:** NVIDIA GeForce RTX 4070 Ti SUPER, 16376 MiB VRAM, compute 8.9, driver
  595.99.02 (CUDA 13.2); `cuLaunchKernel`, `cuLaunchKernelEx`, `cuMemAlloc*` all resolvable in
  `libcuda.so` / `libcuda.so.1`. Fits ~6B FP16 / ~26B 4-bit before KV cache.
- **Toolchain gap:** bpftrace 0.25.0 + bpftool 7.7.0 present, but **no clang, gcc, go or rustc**
  → nothing can be compiled yet. `bcc-tools` absent (0 tools). Phase 0a package installs are
  the next decision.
- **No inference stack:** no llama.cpp / vLLM / torch, no model files on disk. 1.7 TiB free
  disk; huggingface.co reachable (HTTP 200). CPU: i9-14900KF, 32 cores, 30 GiB RAM.
- **Phase 0b note:** GPU-in-container probe not run (needs a local image via `PROBE_IMAGE`),
  but nvidia-container-toolkit 1.20.1 present and the kernel module is loaded.
- **Toolchain installed (author-approved 2026-10-09):** gcc 15.2, clang 21.1.8, cmake 4.2.3,
  ninja, git-lfs, `nvidia-cuda-toolkit` (nvcc 12.4.131). Driver 595.99.02 supports CUDA 13.2,
  so 12.4-built binaries run fine.
- **llama.cpp:** clone `1e6f04a` (2026-10-09) at `~/code/llama.cpp`, built with
  `-DGGML_CUDA=ON`, clang 21.1.8, CMAKE_CUDA_ARCHITECTURES=89-real (Ada 8.9).
- **Model:** `Qwen/Qwen2.5-1.5B-Instruct-GGUF` `qwen2.5-1.5b-instruct-q4_k_m.gguf` (1.1 GB) at
  `~/models/`, sha256 `6a1a2eb6…9407e`.
- **Build gotcha:** llama.cpp's default C compiler drew gcc 15, which rejects a clang-only
  flag (`-Wunreachable-code-return`) → hard error. Fixed by configuring `CC=clang CXX=clang++`.
  Second gotcha: a stale `CMakeCache` made a later reconfigure silently skip the CUDA backend;
  deleting `build/` and reconfiguring fixed it. Watch both on rebuilds.
- **Load generator written:** `tools/loadgen.py` — stdlib-only asyncio client that reads the
  raw socket (per H2's buffering caveat), records per-token arrival timestamps, and emits
  per-request TTFT/ITL ground truth as JSONL.
- **Server running (service `llama-server`):** `llama-server` on `127.0.0.1:8080`, Qwen2.5-1.5B
  Q4_K_M, `-ngl 99`, fully on GPU (1358 MiB VRAM, pid 17305 at smoke time, ctx 4096, 4 slots).
  Loadgen at rest: 3/3 ok — TTFT p50 ≈ 15 ms, ITL mean ≈ 3.4 ms (GPU decode, not a measured
  result — smoke). NOTE: the managed-service `ready` regex did not match
  "listening on http://127.0.0.1:8080" within 120 s; the server was up anyway. On restarts use
  `llama_server: listening` as the readiness regex.
- **Probe-vs-traffic smoke (Phase 0a goal met):** bpftrace `kprobe:tcp_sendmsg /pid == <server>/`
  attached during an 8-request stream; emitted per-event timestamps and counted 288 server
  `tcp_sendmsg` calls vs 256 client tokens (+~32 framing/extra sends, ≈1:1 token/send). No
  latency claim: probe overhead not yet measured (design requirement, Phase 1.3).
- **Bugs found and fixed this session:** (1) loadgen crashed after the first successful stream —
  `r.close()` called on the `StreamReader` (no such method); fixed to `w.close()` +
  `await w.wait_closed()`. (2) bpftrace 0.25.0 renamed `lquantize` → `lhist`, and it is
  `lhist(v, min, max, step)` — four args, not three with (min, step).

## 2026-10-09 — Target host decided; repo scaffolded

- **Target host:** the physical Linux box on the LAN, full admin. Prior plan assumed rented
  cloud GPU (g4dn / RunPod) because the authoring machine is a Mac, which cannot run eBPF at
  any privilege level. A local Linux box with a GPU removes both the VM workaround and the
  cloud spend.
- **Code home:** this repository, standalone (`~/code/ebpf-inference-observability` on the
  authoring machine). Deliberately *not* nested under a shared private research repo, after that
  layout caused an extraction problem on a sibling project.
- **Blocker narrowed.** The original plan marked Phases 0–4 all as GPU-requiring. That is wrong
  for Phases 1, 2 and 4 — `tcp_sendmsg` is host-side, Phase 2 *is* the CPU side, and
  `mmap`/`handle_mm_fault` are host-side. Only Phase 3 needs the GPU. The environment question
  therefore blocks **one phase**, not the project.
- **Phase 0 split into 0a (local, CPU) and 0b (GPU enablement).**
- **Privileges confirmed:** root available, physical box on the LAN.
- **Added to the design:** explicit falsification criteria for H1–H4; a requirement that probe
  overhead be measured before any eBPF-derived number is quoted; the CUDA-graphs confound
  flagged for H1.
- **Pre-flight check performed on the authoring machine** (Docker Desktop's Linux VM) as a
  fallback environment: kernel 6.12.76-linuxkit, BTF present (6.33 MB), tracefs mountable,
  25 sched and 18 net tracepoints, `tcp_sendmsg` resolvable. Recorded only as a fallback — a
  minimal `linuxkit` kernel is strictly worse than the target host for this work.
- **No experiments run yet.** Next action is the host inventory.

## 2026-10-01 — Decisions: server target and environment options

- **Server target decided:** start with **llama.cpp** (1–3B model); vLLM introduced from Phases
  1–3 onward for continuous batching / PagedAttention / CUDA graph experiments.
- **Environment narrowed:** AWS GPU instance (g4dn/g5, Ubuntu 22.04+) considered alongside
  RunPod. Confirmed eBPF runs in-guest, so the Nitro hypervisor is not an obstacle, and Ubuntu
  AMIs ship BTF-enabled kernels. *(Superseded 2026-10-09: the local Linux box is the target.)*
- **Traffic model confirmed:** no production traffic needed. All experiments use a synthetic
  asyncio load generator, which doubles as client-side ground truth (TTFT/ITL) for validating
  eBPF-derived measurements — this is what tests H2.

## 2026-10-01 — Project created

- Formalised the eBPF × inference-serving learning activity into a project.
- Defined the four-layer mental model (network / scheduler / GPU boundary / memory) and five
  experiment phases.
- Hypotheses H1–H4 recorded.
- Open decisions at the time: execution environment, and primary server target.
- No experiments run.
