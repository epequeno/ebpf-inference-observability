# Provenance — what came from where

This repo's docs are derived from a private vault design written on **2026-10-01**, at
`<private research vault>/ebpf-inference-observability/` (four notes: `_Index`, `Overview`,
`Experiments`, `Literature`, `Results-Log`).

The vault is **not** reachable from the target Linux host, so the design was ported here to make
the project self-contained for whoever works on it next. This file records what is the author's
and what was added, so nothing looks original when it is not.

## Ported essentially unchanged

- The research question.
- The **four-layer mental model** (network / scheduler / GPU boundary / memory) and its hook
  mapping.
- The **five phases**, their "claim made visible", their experiments, and their deliverables.
- Hypotheses **H1–H4** (statement and intent).
- **Non-goals:** no production product, no GPU-internal profiling, no benchmarking leaderboard.
- The **differentiation table** against server metrics, nsys/ncu/DCGM, OpenTelemetry and py-spy.
- The **literature list**.
- Server-target decision: **llama.cpp first**, vLLM from Phases 1–3 onward.
- Traffic model: **synthetic asyncio load generator**, doubling as client-side ground truth.

## Changed

| Change | Reason |
|---|---|
| **Phase 1, 2, 4 marked "GPU not required"** | The vault's dependency table marked Phases 0–4 all GPU-requiring. That is wrong for 1 (`tcp_sendmsg` is host-side), 2 (it *is* the CPU side) and 4 (`mmap`/`handle_mm_fault` are host-side). Only Phase 3 needs a GPU. |
| **Phase 0 split into 0a (local, CPU) and 0b (GPU)** | Follows from the above: the environment question blocks one phase, not the project. |
| **Target host: local Linux box, not rented cloud** | Author has a physical Linux box with a GPU on the LAN and full admin. The vault assumed AWS g4dn/g5 or RunPod because the authoring machine is a Mac. This removes the VM workaround and the spend. |
| **Code home: standalone repo** | The vault pointed at a path nested inside a shared private research repo. That layout caused a painful extraction on a sibling project, so this one is standalone from the start. |

## Added (not in the vault)

1. **Falsification criteria for H1–H4** (`docs/hypotheses.md`) — explicit refutation conditions
   and the confounds that would make a positive result meaningless. The vault listed
   hypotheses without stating what would refute them.
2. **Probe-overhead requirement** (`docs/design.md`, cross-cutting) — the vault never mentioned
   that bpftrace perturbs the system it measures. Any eBPF-derived number must come with an
   attached-vs-detached comparison.
3. **CUDA-graphs confound flagged for H1** — graph replay collapses launch counts for reasons
   unrelated to prefill/decode. The vault treated graphs only as a Phase 3.4 experiment.
4. **Numeric tolerances for H2** (10% median error, 0.9 correlation) — **these are placeholders
   and should be set by the author before the first measurement.**
5. **`tools/host-inventory.sh`** — the reconnaissance step the vault's Phase 0 described in prose
   but had not implemented.
6. **"Negative results are results"** framing, including a definition of what a null result
   across all four hypotheses would mean.

## Not authoritative for the author's own record

The **vault remains the author's design record**. This repo is the working copy that the target
host can reach.

Two copies now exist, which is a divergence risk. The suggested convention: **this repo is
authoritative for anything executed, measured, or decided during implementation** (recorded in
`docs/results-log.md`), and the vault keeps long-form notes and motivation. If they drift,
reconcile deliberately and note it in both — do not silently update one.
