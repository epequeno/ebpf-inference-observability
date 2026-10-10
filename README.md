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
> **Target host: the physical Linux box on the LAN.** This repo is **private**.

---

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

## License

Apache-2.0, see [`LICENSE`](LICENSE). `tools/host-inventory.sh` is vendored by the sibling
`full-stack-serving` project under the same license.
