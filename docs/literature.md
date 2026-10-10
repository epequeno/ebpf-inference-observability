# Literature and resources

Working reference list. To be expanded as the project progresses.

## eBPF foundations

- **Brendan Gregg, *BPF Performance Tools* (2019)** — the methodological foundation; especially
  the bpftrace one-liners and the off-CPU analysis chapters. brendangregg.com
- **Brendan Gregg, *Systems Performance* (2nd ed.)** — the USE-method framing that motivates
  layer-by-layer observation.
- **libbpf / CO-RE documentation** — "BPF CO-RE (Compile Once – Run Everywhere)"; portability via
  BTF. Required reading before Phase 3–4.
- **`cilium/ebpf`** (Go) — github.com/cilium/ebpf — the Go toolchain for Phases 4–5.
- **Aya** (Rust) — github.com/aya-rs/aya — alternative toolchain if Go proves awkward.
- **bpftrace reference guide** — github.com/bpftrace/bpftrace — probe types, map functions,
  histogram builtins.

## eBPF in production (networking and security precedents)

- **Cilium** — Kubernetes networking/observability on eBPF; Hubble for flow visibility.
  Reference for Phase 5 cgroup work.
- **Katran (Meta)** — XDP-based L4 load balancer; the architectural precedent for XDP routing of
  inference traffic.
- **Cloudflare's XDP/DDoS work** — production XDP at scale.
- **Tetragon / Falco / Tracee** — runtime security via eBPF; useful for uprobe and LSM patterns.

## GPU / CUDA observability precedents

- **Uprobe-based CUDA tracing** — several open-source efforts trace `cuLaunchKernel` /
  `cuMemcpy*` from user space (search "ebpf cuda uprobe", and GPU tracing in the Parca/Pixie
  ecosystems). Catalogued below (2026-10-10 sweep).
- **NVIDIA nsys / ncu / DCGM** — the non-eBPF GPU profiling baseline; useful for validating
  eBPF-derived launch counts.
- **Datadog / Parca continuous profiling** — production precedent for always-on, low-overhead
  eBPF profiling.
- **eGPU — Yang, Yu, Zheng & Quinn, "EGPU: Extending eBPF Programmability and Observability
  to GPUs", HCDS'25** (DOI 10.1145/3723851.3726984; open-source, merged into
  `eunomia-bpf/bpftime`). JIT-translates eBPF bytecode to PTX and injects it into live GPU
  kernels — the first framework to run eBPF **device-side**. Overturns "eBPF cannot run on the
  GPU" as a hard impossibility (research-grade, not mainline).
- **gpu_ext — "Extensible OS Policies for GPUs via eBPF", arXiv:2512.12615** — safe hooks at
  the driver plus a device-side eBPF runtime executing verified policy inside kernels: the GPU
  as a programmable OS subsystem.
- **SysOM-AI — arXiv:2603.29235 (Alibaba)** — continuous cross-layer eBPF tracing (CPU stack
  profiling, GPU kernel tracing, NCCL instrumentation) at **<0.4% overhead**, deployed on
  80,000+ GPUs for a year; 94 confirmed production diagnoses. The production existence proof
  for low-overhead cross-layer eBPF observation (training-focused).
- **eACGM — arXiv:2506.02007 (IWQoS'25)** — non-instrumented eBPF monitoring across GPU,
  network, CUDA, Python and PyTorch stacks, plus GMM-based anomaly detection.
- **Craun et al. 2024 — "Eliminating eBPF Tracing Overhead on Untraced Processes" (ACM
  SIGCOMM 2024 Workshop on eBPF and Kernel Extensions)** — eBPF tracing overhead lands partly
  on *untraced* co-located processes. Cite in Phase 1's `overhead.md`: the attached-vs-detached
  delta on the server alone is not the whole cost story.
- **Host-side GPU tail-latency telemetry — arXiv:2510.16946** — eBPF host signals (network,
  CPU sched, block I/O) correlated with NVML/NCCL to diagnose GPU tail-latency spikes; the
  closest methodological cousin of this project's network-layer (H2) approach.

## eBPF × LLM inference (the white-box competitive set)

*Added 2026-10-10 after a literature sweep.* Three eBPF systems now occupy the **engine-coupled**
(white-box) space — probes on runtime functions, symbols or structs known from engine source.
None claims what this project claims; see "The gap this project fills".

- **ProfInfer — Zou et al., "ProfInfer: An eBPF-based Fine-Grained LLM Inference Profiler",
  MLSys 2026, arXiv:2601.20755.** Uprobes/uretprobes on llama.cpp/GGML internals
  (`llama_decode`, `ggml_compute_forward*`, `ggml_backend_graph_compute_async`) at
  token/graph/operator granularity; traverses `ggml_tensor` structs; per-operator PMCs;
  `sched_switch`/`sched_wakeup` tracepoints; MoE expert-ID tracing. Mobile/edge, **batch-1**;
  vLLM explicitly out of scope. Decode-speed overhead 2.8–4% (BCC) / 1.7% (libbpf), per-probe
  cost measured via `bpftool` — a calibration reference for Phase 1's attached-vs-detached
  sweep.
- **eInfer — Chu et al., "EInfer: Unlocking Fine-Grained Tracing for Distributed LLM Inference
  with eBPF", 3rd Workshop on eBPF and Kernel Extensions (eBPF'25 @ SIGCOMM), DOI
  10.1145/3748355.3748372.** First eBPF system for end-to-end **per-request tracing across
  distributed LLM pipelines** (CPUs, GPUs, interconnects) without application modification;
  vendor-agnostic; correlates events across accelerators, processes, nodes. Runtime-call-graph
  hooks, not network-derived latency; no external client validation.
- **Herschel — Wang et al., arXiv:2609.40247.** Production model-as-a-service continuous
  optimization via **on-demand attach/detach profiling** of live processes — no engine changes,
  no restarts; bounded ~30 s windows; active-collection overhead **<0.5% TTFT / 7% TPOT**;
  17,000 traces, 120+ model variants, 10+ accelerator types over six months. The industrial-scale
  version of the attach/measure/detach overhead protocol; also a calibration reference for the
  sibling `full-stack-serving` repo's harness-overhead check (ADR 0011, criterion 3).

## Inference serving systems (the systems under observation)

- **vLLM / PagedAttention** — Kwon et al., "Efficient Memory Management for Large Language Model
  Serving with PagedAttention", SOSP '23, arXiv:2309.06180. The KV-cache paging mechanism
  Phase 4 observes.
- **Orca** — Yu et al., OSDI '22. Continuous / iteration-level batching; what Phase 3's idle-gap
  experiment motivates.
- **Sarathi-Serve** — Agrawal et al., OSDI '24. Chunked prefill, prefill/decode interference;
  directly relevant to Phase 1 load sweeps.
- **Splitwise / DistServe and the prefill-decode disaggregation line** — why TTFT and ITL are
  separate workloads; observed empirically in Phases 1 and 3.
- **TensorRT-LLM, TGI, Triton** — alternative servers; comparison targets if time permits.
- **llama.cpp** — mmap weight loading (the Phase 4 lazy-loading case), CUDA graph support.

## Performance analysis of LLM inference (background)

- **LLM inference arithmetic / roofline analyses** — the derivations of why decode is
  memory-bandwidth-bound (weights read per token). Phase 3 makes this visible; these supply the
  theory.
- **FlashAttention, CUDA graphs, kernel fusion** — the optimisations whose launch-count
  signatures Phase 3 measures.

## The gap this project fills

*Reassessed 2026-10-10: the white-box space is now occupied (ProfInfer, eInfer, Herschel). The
gaps below survive all three.*

- **Black-box, not white-box.** Every system above probes engine internals known from source
  (symbols, structs, call graphs). This project observes via **stable ABIs** — socket syscalls
  (H2), `sched` tracepoints (H4 context), `libcuda.so` entry points (H1/H4) — so it works on any
  server binary, closed or uninstrumented included, and ports across engines (llama.cpp *and*
  vLLM).
- **Serving, not batch-1.** ProfInfer is mobile/edge batch-1 and dismisses vLLM; eInfer and
  Herschel target distributed pipelines and operator execution. **Continuous batching under
  concurrent load (H4)** — the central serving mechanism — is observed by none of them.
- **External validation.** Request-level latency derived from **network events alone** (H2),
  with the probe's own overhead measured attached-vs-detached and both checked against an
  **independent client** as ground truth. No system above derives latency from network events,
  and none validates against an external client.
- **The learning narrative.** eBPF as the accelerator for understanding serving architecture,
  each experiment making one architectural claim visible rather than asserted. The bridge —
  "here is what continuous batching looks like in `cuLaunchKernel` inter-arrival times" —
  remains this project's distinctive artifact.
