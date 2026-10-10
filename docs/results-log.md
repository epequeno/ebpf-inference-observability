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

## 2026-10-10 — Host inventory + first probe attach (Phase 0a start)

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
- **Toolchain installed (author-approved 2026-10-10):** gcc 15.2, clang 21.1.8, cmake 4.2.3,
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
