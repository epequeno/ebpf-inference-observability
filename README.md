# ebpf-inference-observability

**Research question:** can eBPF-based *external* observation — network hooks, scheduler
tracepoints, and CUDA API uprobes — reveal the internal mechanics of LLM inference servers
(batching, prefill/decode asymmetry, KV-cache memory pressure) **without modifying or
instrumenting the server**?

The project doubles as a structured path to learning both eBPF and inference-serving
architecture.

**Status: Phase 0 — environment reconnaissance.** No experiments run yet.

Design notes live in a private vault (not published here).

---

## This repo's job right now

It exists so the target host can be prepared from the Linux box itself. The Mac is for
authoring; the **Linux box is the target host** — it has the GPU, and eBPF cannot run on macOS.

## Quick start on the target host

```bash
git clone git@github.com:epequeno/ebpf-inference-observability.git
cd ebpf-inference-observability

# read-only: installs nothing, changes nothing
sudo ./tools/host-inventory.sh > inventory/report.md

git add inventory/report.md && git commit -m "Add host inventory" && git push
```

`sudo` is optional but adds the lock-down, tracefs and `dmesg` checks. Everything else runs
unprivileged. The script is best-effort and safe on a box you care about: missing commands and
unreadable files are reported, never fatal.

## Why an inventory before any code

The design's Phase 0 is one long list of unknowns, and every later phase depends on them:

| Question | Why it gates the design |
|---|---|
| BTF present? (`/sys/kernel/btf/vmlinux`) | Without it, CO-RE and `bpftrace` do not work at all |
| Kernel lockdown / secure boot | Can block kprobes outright, regardless of root |
| Which GPU, how much VRAM? | NVIDIA → Phase 3 works as designed. AMD/Intel → Phase 3 needs rework |
| `cuLaunchKernel` symbol present in `libcuda.so`? | Phase 3's entire uprobe plan depends on it |
| Tracepoint counts (sched / net / kmem) | Phases 1, 2 and 4 are sized by what's available |
| Toolchain state | `bpftrace` / BCC / clang / Go decide which phase can start today |

## Layout

```
tools/host-inventory.sh   # read-only reconnaissance; writes a markdown report
inventory/                # collected host reports (committed)
```

## Where the design lives

The five-phase experiment plan, hypotheses H1–H4, and the four-layer mental model are in the
private vault. Summary of the phases:

| Phase | Claim made visible | Needs GPU? |
|---|---|---|
| 1 — request lifecycle (network) | TTFT and ITL are externally measurable; streaming has a distinct network signature | no |
| 2 — scheduler / CPU | Orchestration overhead and wake patterns are observable | no |
| 3 — GPU via CUDA uprobes | Prefill and decode have distinct kernel-launch signatures | **yes** |
| 4 — memory system | Loading strategy and KV-cache pressure show up in page faults | no |
| 5 — multi-tenant (stretch) | Per-tenant attribution is an eBPF-native problem | optional |

Four of five phases do not need a GPU. Phase 1 alone is enough to meet the project's first
milestone, because TTFT-vs-ITL **is** the externally-observable prefill/decode split.
