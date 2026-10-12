# ebpf-inference-observability

**Can eBPF-based *external* observation — network hooks, scheduler tracepoints, CUDA API
uprobes — reveal the internal mechanics of LLM inference servers without modifying or
instrumenting the server?**

The mechanics in question: continuous batching, prefill/decode asymmetry, KV-cache memory
pressure, CUDA graph effects.

The project is also a structured path to learning both eBPF and inference-serving architecture,
with each experiment designed to make one architectural claim empirically visible rather than
asserted.

> **Status: experimental arc complete (2026-10-10)** — Phases 1–4 plus the vLLM
> cross-engine leg are measured; H1 refuted (cross-engine), H2 confirmed, H3 refuted,
> H4 confirmed. Bottom line: [`docs/findings.md`](docs/findings.md).
> **Target host: a physical Linux box on the author's LAN** — probes only attach there.
> Results were measured on real consumer hardware (RTX 4070 Ti SUPER), not a cloud
> instance. This repo is **public**.

## Headline findings

All measured from outside the server — no instrumentation, no rebuild. Full narrative
with evidence paths: [`docs/findings.md`](docs/findings.md).

- **Token latency from sockets alone (H2 confirmed):** per-connection TTFB/ITL from
  `tcp_sendmsg`, median error ≤5.9% TTFB / ~0.1% ITL against client ground truth across
  C=1→64. The queueing signature is a TTFT staircase — TTFT degrades **532×** across the
  sweep while ITL moves **+44%**: the prefill/decode split plus admission dynamics, from
  the network layer.
- **CUDA graphs economics, cross-engine:** llama.cpp collapses the API launch stream
  **566×** and gains **~12% ITL** (small Q4 kernels are launch-bound); vLLM collapses it
  **9×** for **~0%** (bf16 kernels are too big for launch overhead to matter). Same
  mechanism, opposite latency outcomes — measured from outside on both engines.
- **The CPU side is a futex metronome:** 99.6% of sleeps in `futex_do_wait`, zero
  NVIDIA-driver wait sites, ~60 futex syscalls/token — orchestration that costs nothing
  at this load, which is also why probe overhead stayed invisible.
- **Loading is not lazy anywhere (H3 refuted):** 90–98% of faults land at model *load*
  (prefetch hardcoded in llama.cpp; eager in vLLM) — the real warmup cost on this build is
  CUDA-graph capture, not weight faulting.
- **Probe overhead ≈ 0:** +0.0% ITL attached-vs-control at up to 190k uprobe events/s,
  with the Craun et al. co-located-cost caveat carried on both overhead reports.

---

## Can you run this?

**No — and it is worth saying here rather than in a footnote.** The probes attach to a live inference
server on a physical Linux GPU host with `bpftrace`/bcc and a CUDA-capable card. There is no container
recipe, because the method *is* external observation of a real workload on real hardware.

What is committed instead is the **record**: per-phase outputs under [`results/`](results/) (`phase1` …
`phase4`, one timestamped directory per run), the consolidated narrative in
[`docs/findings.md`](docs/findings.md), the hypotheses with their falsification criteria in
[`docs/hypotheses.md`](docs/hypotheses.md), and a chronological log in
[`docs/results-log.md`](docs/results-log.md), newest first.

To repeat any of it you need the host — [`docs/environment.md`](docs/environment.md). The probes are in
[`tools/`](tools/) (`ttft.bt`, `phase2-threads.bt`, `phase3-launches.bt`, `phase3-allocs.bt`,
`phase4-faults.bt`), with their sweep and validation scripts beside them.

## Start here

| If you are… | Read |
|---|---|
| **an agent** picking this up | [`AGENTS.md`](AGENTS.md) first — rules, status, next action |
| looking for the design | [`docs/design.md`](docs/design.md) — question, four-layer model, five phases |
| looking for the claims under test | [`docs/hypotheses.md`](docs/hypotheses.md) — H1–H4 with falsification criteria |
| setting up the machine | [`docs/environment.md`](docs/environment.md) — target host, Phase 0a/0b, GPU policy |
| wanting the bottom line | [`docs/findings.md`](docs/findings.md) — the consolidated narrative, layer by layer |
| wondering what's been learned | [`docs/results-log.md`](docs/results-log.md) — newest first |
| wanting the background | [`docs/literature.md`](docs/literature.md) |

## Next action

**Paused (2026-10-10).** The experimental arc is complete and consolidated in
[`docs/findings.md`](docs/findings.md); open follow-ups are listed there ("Open
threads" — H1 on slower hardware, H2/H4 on vLLM, Phase 5, a symbol-rich rebuild
decision). All results are committed under `results/`.

Resuming on the target host: restart llama.cpp with the flags in the 2026-10-09
results-log entry (the graphs-OFF `build-off/` binary was the last under test;
readiness regex `llama_server: listening`), or relaunch vLLM from the command in
`results/vllm/20261010T202713Z/meta.txt`. Runbook: top of `docs/results-log.md`.

## The shape of the work

An inference server is four systems stacked, each with its own eBPF hooks:

| Layer | eBPF hooks | Phase |
|---|---|---|
| Network service | `tcp_sendmsg` / `tcp_recvmsg` kprobes | 1 |
| Scheduler / batcher | `sched` tracepoints, off-CPU profiling | 2 |
| GPU driver consumer | uprobes on `libcuda.so` (`cuLaunchKernel`, `cuMemAlloc`, …) | 3 |
| Memory system | `mmap` / `madvise` tracing, `handle_mm_fault`, RSS | 4 |

**The constraint that shapes everything: eBPF cannot run on the GPU.** GPU visibility is
indirect, via the user-space CUDA API — which turns out to reveal launch rhythm, transfer
overhead and allocation behaviour.

**Four of the five phases do not need a GPU.** Phase 1 alone meets the project's first
milestone, because TTFT-vs-ITL *is* the externally observable prefill/decode split.

## Layout

```
AGENTS.md                 # instructions for an agent working in this repo
README.md                 # this file
docs/
  design.md               # research question, mental model, phases, non-goals
  hypotheses.md           # H1-H4: predictions, falsification criteria, confounds
  environment.md          # target host, Phase 0a/0b, GPU-vs-CPU, privileges
  literature.md           # references
  results-log.md          # chronological log, newest first
  findings.md             # consolidated narrative (the distinctive artifact)
  provenance.md           # what came from the private vault vs. what was added later
tools/
  host-inventory.sh       # read-only host reconnaissance -> markdown report
  loadgen.py              # asyncio streaming load + TTFT/ITL/TTFB ground truth
                          #   (--api llamacpp|openai, --gap arrival pacing)
  ttft.bt                 # Phase 1 probe: per-connection TTFB/ITL from tcp_sendmsg
  phase1-sweep.sh         # attached-vs-detached load sweep (probe-overhead requirement)
  phase1-validate.py      # H2 validation: eBPF vs ground truth + probe cost
  phase2-threads.bt       # Phase 2: thread-wake + sync census (sched/futex/glibc)
  phase3-launches.bt      # Phase 3: CUDA launch/graph/memcpy census (libcuda uprobes)
  phase3-allocs.bt        # Phase 3: cuMemAlloc/Free census
  phase4-faults.bt        # Phase 4: page-fault + mmap/madvise census
inventory/                # collected host reports
results/                  # committed artifacts: phase1/, phase2/, phase3/, phase4/, vllm/
```

## Why this is worth doing

Most eBPF material targets SRE-style host observability; most inference-serving material is
papers and benchmarks. The bridge — *"here is what continuous batching looks like in
`cuLaunchKernel` inter-arrival times, measured from outside the process"* — is the artifact this
project is trying to produce. See `docs/design.md` for how it differs from nsys/ncu, DCGM,
OpenTelemetry and py-spy.

## Reusable tools

The probes are generic — parameterized by pid/comm, attached via the stable
`libcuda.so.1` soname — not tied to this repo's host or engine. Zero dependencies beyond
`bpftrace`; attach to any inference server (llama.cpp, vLLM, anything HTTP/SSE over TCP)
without touching it:

| Tool | What it gives you |
|---|---|
| `tools/ttft.bt` | Per-connection TTFB/ITL from `tcp_sendmsg` — token latency with zero server instrumentation |
| `tools/phase1-validate.py` + `phase1-sweep.sh` | The attached-vs-detached overhead pattern: validate any probe against client ground truth before quoting its numbers |
| `tools/phase3-launches.bt` | CUDA API census from `libcuda` uprobes — launch rhythm, graph capture/replay counts, per-step structure |
| `tools/phase3-allocs.bt` | `cuMemAlloc` census — KV-pool pre-allocation visibility |
| `tools/phase4-faults.bt` | Page-fault timing at load vs first inference — is model loading lazy or eager |
| `tools/phase2-threads.bt` | sched/futex census — what the server's CPU threads actually do between launches |
| `tools/loadgen.py` | asyncio streaming load + TTFT/ITL/TTFB ground truth, with `--gap` bursty-arrival pacing |

Each probe header records the hard-won kernel/driver facts that shaped it — the
stable-soname attach (vs. the alias triple-count), vLLM's `python3`/`pt_*` thread names
needing tgid filtering, container uprobes over the host bind-mount. Failures and dead
ends are logged in `docs/results-log.md`.

## License

Apache-2.0, see [`LICENSE`](LICENSE). `tools/host-inventory.sh` is vendored by the sibling
`full-stack-serving` project under the same license.
