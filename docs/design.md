# Design

## The research question

Can eBPF-based **external** observation — network hooks, scheduler tracepoints, and CUDA API
uprobes — reveal the internal mechanics of LLM inference servers (batching, prefill/decode
asymmetry, KV-cache memory pressure) **without modifying or instrumenting the server**?

Inference servers (vLLM, TensorRT-LLM, Triton, llama.cpp, TGI) are black boxes. Reading their
source tells you what they are *supposed* to do; eBPF lets you watch what they *actually* do,
live, without recompiling or restarting them.

The project is also a deliberate **learning vehicle**: eBPF as an accelerator for understanding
inference-serving architecture, with each experiment designed to make one architectural claim
empirically visible rather than asserted. Learning runs in both directions:

- **eBPF skills:** bpftrace one-liners → BCC tools → CO-RE programs with maps and histograms
  (`cilium/ebpf` in Go, or libbpf in C).
- **Serving intuition:** continuous batching, prefill/decode disaggregation, KV-cache paging,
  CUDA graphs — each motivated by something observed.

## The four-layer mental model

An inference server is four systems stacked. Each maps to a set of eBPF hooks.

| Layer | What it does | eBPF hooks |
|---|---|---|
| 1. Network service | Accept requests, stream tokens (SSE) | kprobes on `tcp_sendmsg` / `tcp_recvmsg`, sockops, XDP/TC |
| 2. Scheduler / batcher | Queueing, continuous batching, prefill/decode split | `sched` tracepoints, off-CPU profiling, uprobes on server functions |
| 3. GPU driver consumer | CUDA kernel launches, memory management | uprobes on `libcuda.so` / `libcudart.so` (`cuLaunchKernel`, `cuMemAlloc`, `cuMemcpy*`) |
| 4. Memory system | Model weight loading, KV-cache paging | `mmap` / `madvise` syscall tracing, `handle_mm_fault`, RSS tracking |

**The constraint that shapes everything: eBPF cannot run on the GPU.** GPU visibility is
indirect — by tracing the user-space CUDA API, which turns out to reveal a great deal (launch
rhythm, transfer overhead, allocation behaviour). This is a deliberate boundary, not a
limitation to be worked around.

## What we expect to make visible

1. **Why continuous batching exists** — without it, gaps between requests are visible in
   `cuLaunchKernel` inter-arrival times.
2. **Why PagedAttention / KV-cache paging mattered** — KV-cache growth under concurrent load
   is visible in allocation and RSS patterns.
3. **Why decode is memory-bandwidth-bound** — the decode loop shows hundreds of tiny kernel
   launches per second, each dominated by weight loading rather than math.
4. **Why CUDA graphs and kernel fusion help** — launch overhead and launch count are directly
   measurable.
5. **Why prefill/decode disaggregation is a thing** — the two phases have different signatures
   at every observed layer.
6. **Why warmup requests exist** — mmap lazy-loading produces page-fault storms on first
   inference.

## Phases

Each phase has a **claim it makes visible**. A phase succeeds when the claim is observed
empirically and logged in `results-log.md`. Phases are ordered by eBPF difficulty (bpftrace
one-liners → CO-RE programs) and by depth into the server.

### Phase 0 — Environment

**Goal:** a Linux host with eBPF tooling, a running inference server, and a load generator.

| | |
|---|---|
| **0a — local (this host)** | The Linux box on the LAN. Tooling + `llama.cpp` on CPU. Unblocks Phases 1, 2, 4. |
| **0b — GPU enablement** | Only needed for Phase 3. On this design, that is the *same box* if it has a usable NVIDIA GPU; otherwise a rented instance. |

**Deliverable:** `inventory/report.md` (produced by `tools/host-inventory.sh`) plus a documented
smoke test — e.g. `bpftrace -e 'tracepoint:syscalls:sys_enter_openat { @[comm] = count(); }'`
against the server process.

### Phase 1 — The request lifecycle (network hooks)

**Claim:** TTFT and ITL are externally measurable; streaming token responses have a distinctive
network signature.

1. **Connection census** — `tcplife` / `tcptop` on the server port. Streaming SSE connections
   show a trickle pattern (many small sends) vs. bulk responses.
2. **TTFT/ITL from `tcp_sendmsg`** — kprobe filtered to the server process/port; timestamp the
   first send vs. subsequent sends per connection.
3. **Validation** — compare eBPF-derived TTFT/ITL against client-side ground truth from the
   load generator. *This tests H2 and is the phase's real deliverable.*
4. **Load sweep** — re-run at 1, 4, 16, 64 concurrent requests; watch TTFT degrade while ITL
   stays flatter. Empirical motivation for the prefill/decode split.

**Deliverable:** TTFT/ITL histograms at several concurrency levels, with documented error
bounds against ground truth.

> **Phase 1 alone satisfies the project's first milestone.** TTFT-vs-ITL *is* the externally
> observable prefill/decode split, so this phase needs no GPU at all.

### Phase 2 — The scheduler and CPU side

**Claim:** The server's orchestration overhead — thread wake patterns, lock contention, CPU
oversubscription — is observable and often surprising.

1. **Thread behaviour** — `sched:sched_switch` + `sched:sched_wakeup` on server threads.
   Compare llama.cpp's decode-loop threads with vLLM's async event loop.
2. **Off-CPU analysis** — BCC `offcputime -p <pid>`. Hypothesis: non-trivial time in
   synchronisation and IPC, not GPU waits as naively expected.
3. **Uprobes on server functions** — hook scheduler-relevant symbols to build a call-frequency
   and latency view of the batching loop.
4. **Flame graphs** — BCC `profile` during sustained decode.

**Deliverable:** off-CPU report + thread-wake characterisation; notes on where orchestration
overhead actually lives.

### Phase 3 — GPU visibility via CUDA uprobes

**Claim:** Prefill and decode have distinct kernel-launch signatures; decode is dominated by
many small launches; CUDA graphs visibly collapse launch counts. (H1, H4.)

1. **Launch counting** — uprobe on `cuLaunchKernel` in `libcuda.so`; count/sec and inter-arrival
   histogram during (a) a single prefill, (b) steady-state decode. *This single experiment
   teaches the core architectural fact of LLM serving.*
2. **Transfer detection** — uprobes on `cuMemcpyHtoD` / `cuMemcpyDtoH` — catch host↔device
   transfers during decode (logits to CPU, sampling overhead).
3. **Allocation watch** — uprobes on `cuMemAlloc` / `cuMemFree` during startup; watch the KV
   cache grab its headroom.
4. **CUDA graph comparison** — graphs on vs. off (`--enforce-eager`) → the launch-count delta
   quantifies why graphs matter. **Also required as an H1 confound control.**
5. **Idle-gap analysis (H4)** — inter-`cuLaunchKernel` gaps under bursty single-stream load vs.
   continuous batching.

**Known friction points:**
- CUDA library symbol versioning — may need `objdump -T` on the exact `libcuda.so` and offset
  arithmetic from `/proc/<pid>/maps`. `tools/host-inventory.sh` section 4 checks symbol
  presence before you start.
- Uprobe overhead at high launch rates — **aggregate in-kernel (BPF maps), never stream every
  event to user space.**

**Deliverable:** launch-rate time series prefill vs. decode; memcpy and alloc event logs;
graphs-on/off comparison table.

### Phase 4 — The memory system

**Claim:** Model-loading strategy and KV-cache pressure are visible in page-fault and allocation
patterns; warmup requests exist because of lazy loading. (H3.)

1. **Load-time tracing** — trace `mmap` / `madvise` while the model loads. llama.cpp: large lazy
   mappings. PyTorch/vLLM: eager `load_state_dict` reads. The difference should be directly
   visible.
2. **First-inference fault storm** — `kprobe:handle_mm_fault` (or the `kmem:mm_page_alloc`
   tracepoint) + fault counting during the first request vs. steady state.
3. **KV-cache growth** — RSS + page-cache tracking under increasing concurrency × context
   length; llama.cpp contiguous cache vs. vLLM PagedAttention block allocation.
4. **Under pressure** — induce memory pressure; observe OOM behaviour / recompute events (vLLM
   preemption) from outside.

**Deliverable:** fault-storm quantification; KV-cache growth curve vs. (concurrency, context
length); mmap-vs-eager comparison notes.

### Phase 5 — Multi-tenant and production (optional / stretch)

**Claim:** The infrastructure layer around inference (per-tenant attribution, L4 routing) is an
eBPF-native problem.

1. **Cgroup-level attribution** — run the server in a container; cgroup-scoped eBPF attributes
   network/CPU per tenant or replica.
2. **XDP/TC observation** — attach at XDP to see the earliest routing point (Katran-style).
3. **Correlation exercise (hard)** — join network-layer request events with CUDA launch patterns
   for per-request GPU-cost attribution.

## Phase dependencies

| Phase | Blocks on | Est. time | GPU needed? |
|---|---|---|---|
| 0a — local env | — | 0.5 day | **no** |
| 0b — GPU enablement | only if the box's GPU is unsuitable | 0.5 day | yes |
| 1 — network | 0a | 1 day | **no** |
| 2 — scheduler / CPU | 0a | 1–2 days | **no** |
| 4 — memory | 0a | 1–2 days | **no** |
| 3 — CUDA uprobes | 0a (+ 0b) | 2–3 days | **yes** |
| 5 — multi-tenant | 1, 3 | 2+ days | optional |

> **Correction to the original plan.** The vault version marked Phases 0–4 all as
> GPU-requiring. That is false for 1, 2 and 4: `tcp_sendmsg` is host-side, Phase 2 *is* the CPU
> side, and `mmap`/`handle_mm_fault` are host-side. Only Phase 3 needs the GPU. This matters
> because it means the environment question blocks **one phase**, not the whole project.

**Ordering note:** with the GPU local, front-loading the CPU phases is no longer necessary on
cost grounds. Phase 1 is still the cheapest first datum and the fastest route to the milestone.

## Cross-cutting requirements

### Probe overhead must be measured, not assumed

bpftrace at high event rates perturbs the system being measured. **Before quoting any
eBPF-derived number, measure the probe's own cost** by running the same workload attached vs.
detached, and report the delta. A number produced by a probe whose overhead is unmeasured is
not a result.

### Negative results are results

Each hypothesis in `hypotheses.md` has an explicit falsification criterion, stated **before**
the experiment. If H1 fails — if prefill and decode are not distinguishable from launch
signatures — that is a finding worth recording and publishing, not a failure to hide. Define
the null up front so it cannot be reinterpreted after the fact.

## Scope and non-goals

- **Not** building a production observability product. The deliverable is understanding,
  documented in `results-log.md`.
- **Not** GPU-internal profiling (SM occupancy, tensor-core utilisation) — that requires
  nsys/ncu. We stop at the CUDA API boundary.
- **Not** benchmarking servers against each other for a leaderboard. Comparisons serve
  understanding mechanisms only.

## Differentiation from existing approaches

| Approach | What it gives you | What eBPF adds |
|---|---|---|
| Server's own metrics/logs | What the developers chose to expose | Everything they didn't; identical across servers |
| NVIDIA nsys / ncu / DCGM | Deep GPU-internal detail | A correlated host-side view: network, CPU, memory *and* CUDA API in one framework; always-on, production-safe |
| APM / distributed tracing | Request-level spans, *if instrumented* | Zero-instrumentation visibility; works on any server binary |
| Python profiling (py-spy) | Python call stacks | Crosses Python → C++ → driver, and adds kernel-side events |

**The distinctive artifact:** a single coherent, externally-observed narrative of what an
inference server does, layer by layer, with reproducible experiments. Most eBPF material
targets SRE host observability; most serving material is papers and benchmarks. The bridge —
"here is what continuous batching looks like in `cuLaunchKernel` inter-arrival times" — is the
contribution.
