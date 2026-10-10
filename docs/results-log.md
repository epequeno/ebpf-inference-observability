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
