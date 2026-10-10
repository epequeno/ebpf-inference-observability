# Environment

## Target host

**The physical Linux box on the LAN.** That machine is the target host for all phases — it is
where the probes attach, where the inference server runs, and where results are produced.

| | |
|---|---|
| Access | LAN, physical, **full admin** (root available) |
| Why not the Mac | eBPF requires a Linux kernel. macOS cannot run it, at any privilege level. |
| Why this box | It has the GPU, which Phase 3 requires |

The Mac is an **authoring** machine only: docs, scripts, and reading. Everything that touches a
probe runs on the Linux box.

## The host inventory is the first step

`tools/host-inventory.sh` is read-only and installs nothing. Run it on the target host and
commit the output:

> **This is the canonical copy.** The same script is vendored by the `full-stack-serving`
> project, because both run on this host and two scripts would eventually disagree about the
> same machine. Its sections 7 and 10 cover serving-stack prerequisites (container runtime,
> k3s, helm, terraform, GPU-in-container) that this project does not need — ignore them. Edit
> the script **here**; the other copy is refreshed from this one.

```bash
sudo ./tools/host-inventory.sh > inventory/report.md
git add inventory/report.md && git commit -m "Add host inventory" && git push
```

It answers, in one pass, the questions the design is currently guessing at:

| Question | Why it gates the design |
|---|---|
| BTF present (`/sys/kernel/btf/vmlinux`)? | Without it, CO-RE and bpftrace do not work **at all**. Hard blocker. |
| Kernel lockdown / secure boot state | In `[integrity]` or `[confidentiality]`, kprobes are blocked **regardless of root**. Hard blocker. |
| Which GPU, how much VRAM? | NVIDIA → Phase 3 proceeds as designed. AMD/Intel → Phase 3 needs rework (HIP/ROCm uprobes, different assertions) |
| `cuLaunchKernel` resolvable in `libcuda.so`? | Phase 3's entire uprobe plan depends on it |
| Tracepoint counts (sched / net / kmem) | Sizes Phases 1, 2 and 4 |
| Toolchain state (bpftrace, clang, Go, headers) | Decides which phase can start today |

**Until that report exists, treat every hardware claim in these docs as an assumption.**

## Phase 0a vs 0b

The original plan treated "environment" as one blocking step. It is two, and only the second
needs a GPU.

- **Phase 0a — this host.** Install eBPF tooling, build `llama.cpp`, run a small model **on
  CPU**, stand up the load generator, confirm a probe attaches and emits timestamps.
  Unblocks Phases 1, 2 and 4.
- **Phase 0b — GPU enablement.** Only needed for Phase 3. If the box's GPU is a usable NVIDIA
  device, this is nearly free (driver + CUDA already present). Otherwise it means renting.

## GPU vs CPU

**Policy: prefer the GPU when it is suitable, CPU otherwise.** "Suitable" means, concretely:

| Condition | Consequence |
|---|---|
| NVIDIA + kernel driver loaded + `cuLaunchKernel` in `libcuda.so` | Phase 3 is viable; prefer GPU for all phases |
| NVIDIA but too little VRAM for a 1–3B model | Quantise (Q4/Q5) or use a smaller model; phases still viable |
| AMD / Intel GPU | Phase 3 needs rework — ROCm/HIP uprobes (`hipLaunchKernel`) or vendor tracing. **CUDA-specific hypotheses do not transfer.** Phases 1, 2, 4 unaffected. |
| No usable GPU | Phases 1, 2, 4 run on CPU. Phase 3 requires a rented NVIDIA instance (g4dn / RunPod, ~$0.35–0.55/hr; short sessions keep total spend well under $50). |

Note for CPU fallback: slow inference **helps** Phase 1. A lower token rate makes ITL easier to
resolve from `tcp_sendmsg` timestamps, so a CPU-only start is not a degraded start.

## Privileges and safety

eBPF needs root or `CAP_BPF` + `CAP_PERFMON`. This box is disposable as a research target, which
is the right posture for Phase 4, which deliberately induces memory pressure.

Rules for work on this box:

- **Do not install anything without asking.** The inventory script is deliberately read-only.
- **Do not modify kernel boot parameters** without asking — lockdown/secure-boot changes are
  disruptive and may be irreversible without physical access.
- **Record anything you change.** `results-log.md` is the audit trail.
- Prefer `bpftrace` scripts and in-kernel aggregation over per-event streaming to user space.

## Expected tooling (exact set depends on the inventory)

| Layer | Tools |
|---|---|
| Tracing | `bpftrace`, `bcc-tools`, `bpftool`, `trace-cmd` |
| Build (Phase 3+) | `clang`, `llvm`, `libbpf-dev`, kernel headers |
| Language toolchains | Go (`cilium/ebpf`, Phases 4–5), or Rust (`aya`) as an alternative |
| Server | `llama.cpp` first (simpler; mmap loading visible), then `vLLM` |
| Load generation | a small Python asyncio client that records TTFT/ITL as ground truth |

## Open questions blocked on the inventory — answered 2026-10-09

1. **Lockdown-free and BTF-enabled?** BTF present; SecureBoot enabled but probes attach and
   fire (verified with a live `tcp_sendmsg` kprobe). Not a blocker.
2. **NVIDIA? How much VRAM?** RTX 4070 Ti SUPER, 16 GiB → Phase 3 as designed; GPU preferred
   for all phases (Policy: prefer GPU when suitable).
3. **`cuLaunchKernel` resolvable?** Yes — in both `libcuda.so` and `libcuda.so.1`, with
   `cuLaunchKernelEx`, `cuGraphLaunch`, `cuMemAlloc*`/`cuMemFree*` (full list in
   `inventory/report.md` §4).
4. **Disk for a 1–3B model plus builds?** Yes — 1.7 TiB free; model (1.1 GB) is downloaded.
5. **Toolchain present?** bpftrace/bpftool were; now gcc 15, clang 21, nvcc 12.4, cmake 4.2,
   ninja, git-lfs installed (author-approved) and llama.cpp 0.6.0-dev is built with CUDA.
   **`bpfcc-tools` 0.35.0 is present** (132 tools — the old "absent" reading was a
   detection bug: the inventory only looked at `/usr/share/bcc/tools`, not Ubuntu's
   `/usr/sbin/*-bpfcc`). On kernel 7.0.0-38 (2026-10-10): `profile-bpfcc` does not
   compile (kernel-7.0 header static_assert), and `offcputime-bpfcc` traces but hangs
   in report generation at Phase-2 event rates — bpftrace substitutes were used for
   Phase 2; see `docs/results-log.md`.

6. **Which llama.cpp binary is the server?** Two builds exist (2026-10-10): `build/` =
   CUDA graphs ON (the 0.6.0-dev shipping default) and `build-off/` =
   `GGML_CUDA_GRAPHS=OFF` (author-approved H1 confound control). The running server is
   the graphs-OFF binary; results-log entries record which binary each measurement used.

7. **vLLM?** Introduced 2026-10-10 as an observation target: `vllm/vllm-openai:v0.23.0`
   container (image already local from the sibling `full-stack-serving` repo; weights
   from its `hf-cache` docker volume — no downloads), run with `--gpu-memory-utilization
   0.45` to coexist with llama-server. Not a host install: no pip packages, no system
   changes; the container is removed after each experiment session (relaunch command in
   the results artifacts).
