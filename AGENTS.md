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

**Phase 0a. No experiments have been run.** The immediate task is the host inventory:

```bash
sudo ./tools/host-inventory.sh > inventory/report.md
git add inventory/report.md && git commit -m "Add host inventory" && git push
```

That report determines what is possible: BTF and kernel lockdown decide whether eBPF works at
all; the GPU decides whether Phase 3 is available; the toolchain decides which phase can start.

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
