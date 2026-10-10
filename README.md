# ebpf-inference-observability

**Can eBPF-based *external* observation — network hooks, scheduler tracepoints, CUDA API
uprobes — reveal the internal mechanics of LLM inference servers without modifying or
instrumenting the server?**

The mechanics in question: continuous batching, prefill/decode asymmetry, KV-cache memory
pressure, CUDA graph effects.

The project is also a structured path to learning both eBPF and inference-serving architecture,
with each experiment designed to make one architectural claim empirically visible rather than
asserted.

> **Status: Phase 0a — environment reconnaissance. No experiments run.**
> **Target host: the physical Linux box on the LAN** (it has the GPU; macOS cannot run eBPF).
> This repo is **private**.

---

## Start here

| If you are… | Read |
|---|---|
| **an agent** picking this up | [`AGENTS.md`](AGENTS.md) first — rules, status, next action |
| looking for the design | [`docs/design.md`](docs/design.md) — question, four-layer model, five phases |
| looking for the claims under test | [`docs/hypotheses.md`](docs/hypotheses.md) — H1–H4 with falsification criteria |
| setting up the machine | [`docs/environment.md`](docs/environment.md) — target host, Phase 0a/0b, GPU policy |
| wondering what's been learned | [`docs/results-log.md`](docs/results-log.md) — newest first |
| wanting the background | [`docs/literature.md`](docs/literature.md) |

## Next action

The host inventory is **done and committed** — `inventory/report.md` (regenerate anytime with
`sudo ./tools/host-inventory.sh > inventory/report.md`; it is read-only and installs nothing).
Its end-of-report **readiness verdict** gates the approach; see `docs/environment.md`.

Phase 0a is in progress: llama.cpp is built (CUDA) and serving Qwen2.5-1.5B on the target host,
and `tools/loadgen.py` produces TTFT/ITL ground truth. The next milestone is **Phase 1**
(`docs/design.md`): per-connection TTFT/ITL from `tcp_sendmsg` timestamps, validated against
that ground truth, with the probe's own overhead measured (attached vs detached) before any
number is quoted.

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
  provenance.md           # what came from the private vault vs. what was added later
tools/
  host-inventory.sh       # read-only host reconnaissance -> markdown report
  loadgen.py              # asyncio streaming load + TTFT/ITL ground truth (JSONL)
inventory/                # collected host reports
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
