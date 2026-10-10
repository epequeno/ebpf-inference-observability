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

## Open questions blocked on the inventory

1. Is the kernel lockdown-free and BTF-enabled? *(Hard blocker if not.)*
2. NVIDIA or not, and how much VRAM? *(Decides Phase 3 and the CPU/GPU split.)*
3. Are `libcuda.so`'s launch symbols resolvable, and at what names? *(Decides how much time
   Phase 3 burns on symbol discovery.)*
4. Is there enough disk for a 1–3B model (a few GB) plus builds?
5. Is the toolchain already present, or does 0a begin with package installs?
