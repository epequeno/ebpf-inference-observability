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
  ecosystems). To be catalogued during Phase 0–1; symbol-visibility issues documented there.
- **NVIDIA nsys / ncu / DCGM** — the non-eBPF GPU profiling baseline; useful for validating
  eBPF-derived launch counts.
- **Datadog / Parca continuous profiling** — production precedent for always-on, low-overhead
  eBPF profiling.

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

- Most eBPF material targets SRE-style host observability; little connects it to
  inference-serving internals as a **learning narrative**.
- Most inference-serving material is papers and benchmarks, not live external observation.
- The bridge — "here is what continuous batching looks like in `cuLaunchKernel` inter-arrival
  times" — is the project's distinctive artifact.
