# Hypotheses

Four hypotheses, each with a **prediction**, a **falsification criterion** stated before the
experiment, and the **confounds** that would make a positive result meaningless.

The falsification criteria are the point of this file. Without them, any observation can be
narrated into a confirmation afterwards. Fix the null up front.

---

## H1 — Prefill and decode have distinguishable eBPF-visible signatures

> Few large kernel launches during prefill vs. many small ones during decode — the single most
> important architectural fact of LLM serving, observable from outside the process.

**Prediction.** During prefill, `cuLaunchKernel` inter-arrival gaps are large and the launch
rate is low. During steady-state decode, the launch rate is high and regular (roughly one
batch-step's worth of launches per token step). The two inter-arrival distributions should be
separable without knowing anything the server tells us.

**Falsification.** Any of:

- The prefill and decode inter-arrival distributions overlap substantially (e.g. a KS test
  fails to separate them, or the medians differ by less than the within-phase spread).
- The observed launch counts track wall-clock time rather than token progression — i.e. we are
  measuring the sampler, not the model.
- No separable structure appears at all, meaning the CUDA API boundary does not expose the
  prefill/decode asymmetry for this server.

**Confounds.**
- **CUDA graphs.** Graph capture/replay collapses launch counts for reasons unrelated to
  prefill vs. decode. *H1 must be tested with graphs disabled* (`--enforce-eager` for vLLM,
  equivalent for llama.cpp). Graph mode then becomes its own comparison (Phase 3.4).
- **Launch batching inside the runtime.** If the driver coalesces launches, API-level counts
  understate kernel-level reality. Report API-level counts and label them as such.
- **Sampling and logit transfer.** `cuMemcpyDtoH` during decode is sampling overhead, not
  model compute. Count it separately.

**Test:** Phase 3.1. Requires an NVIDIA GPU with `cuLaunchKernel` resolvable in `libcuda.so`
(checked by `tools/host-inventory.sh` section 4).

---

## H2 — TTFT and ITL are measurable from `tcp_sendmsg` timing alone

> Request-level latency is recoverable from network events, with no application instrumentation.

**Prediction.** Per-connection, timestamps of `tcp_sendmsg` calls on the server's listening port
recover TTFT (first send after request arrival) and ITL (inter-send gaps thereafter), matching
client-side ground truth within a small tolerance.

**Falsification.** Any of:

- Median absolute error against client-side ground truth exceeds **10%**, or the correlation
  between eBPF-derived and client-observed ITL is below **0.9**.
- The error is not stable across concurrency levels — i.e. it degrades as load rises, which
  would make the measurement unusable exactly when it matters.
- Send boundaries do not correspond to token boundaries at all (multiple tokens coalesced into
  one segment, or one token split across segments) so thoroughly that per-token timing is
  unrecoverable.

> **Set the tolerance before the first measurement.** 10% / 0.9 are the numbers to beat;
> adjust them in this file before running, not after seeing the result.

**Confounds.**
- **TCP coalescing and Nagle.** Multiple tokens may share a segment. Mitigate by disabling
  Nagle on the client and recording segment sizes.
- **Write paths that bypass `tcp_sendmsg`.** `sendfile`/`splice` do not necessarily go through
  it. Verify the server's actual send path before trusting the probe.
- **TLS.** Does not prevent timing measurement, but removes any chance of reading payload
  structure. Note it; do not rely on payload inspection.
- **Client-side buffering.** The ground truth must be measured at the client's socket read, not
  at the HTTP library's iterator, or the comparison is apples-to-oranges.

**Test:** Phase 1.3. **No GPU required.**

---

## H3 — Model-loading strategy is visible in page-fault patterns, and explains warmup

> Warmup requests exist because mmap'd weights are faulted in lazily on first use.

**Prediction.** With mmap-based loading (llama.cpp), a cold start produces a burst of
page faults concentrated in the **first inference**, decaying sharply afterwards. With eager
loading (PyTorch/vLLM `load_state_dict`), faults concentrate at **load time**, and the first
inference looks like steady state.

**Falsification.** Any of:

- Fault counts during first inference are not distinguishable from steady state, for the
  mmap-based server.
- Faults appear at load time for the mmap-based server too, meaning loading is not actually
  lazy and the warmup explanation is wrong.
- The pattern is explained entirely by page-cache state we failed to control.

**Confounds.**
- **Page cache.** Must be dropped between runs (`echo 3 > /proc/sys/vm/drop_caches`) or the
  second run has no faults and the comparison is meaningless. Record whether caches were
  dropped; a run without it does not count.
- **Other tenants.** Anything else on the box touching the same pages invalidates the count.
- **Transparent huge pages / readahead** can move faults between counters.

**Test:** Phase 4.2. **No GPU required.**

---

## H4 — Decode-loop idle gaps are visible as launch gaps, and continuous batching removes them

> The motivation for continuous batching, observed from outside.

**Prediction.** Under bursty single-stream load, inter-`cuLaunchKernel` gaps during decode track
the gaps between arrivals. With continuous batching and concurrent load, those gaps shrink or
disappear as the batch stays occupied.

**Falsification.** Any of:

- Inter-launch gaps do not change measurably between single-stream and concurrent load.
- Gaps correlate with something other than batch occupancy (e.g. fixed timer or polling
  interval), which would mean we are observing the scheduler's tick, not batching.
- The server under test does not exhibit the pathology (some servers already batch by default),
  in which case the experiment needs a non-batching baseline to be meaningful.

**Confounds.**
- Server defaults may already enable continuous batching — establish a genuinely non-batching
  baseline first.
- Request arrival pattern dominates. Control it with the load generator; do not use wall-clock
  traffic.

**Test:** Phase 3.5. Requires an NVIDIA GPU, and Phase 3.1 working.

---

## What a null result looks like

If every hypothesis above is refuted, the finding is:

> *The CUDA API boundary, host-side network timing, and page-fault accounting do not expose
> prefill/decode asymmetry for this class of server, at least not at the granularities tested.*

That is a publishable negative result and would be recorded in `results-log.md` as such. It
would also be more interesting than a pile of confirmations, because the premise of external
observability for inference serving is widely assumed and rarely tested at this level.

**Do not reframe a null as a partial success.** If the tolerance was 10% and the error was 30%,
that is a refutation of H2, not a promising direction.

---

## Status

| Hypothesis | Status | Blocked on |
|---|---|---|
| H1 | not started | Phase 0a + 0b (GPU) |
| H2 | not started | Phase 0a |
| H3 | not started | Phase 0a |
| H4 | not started | Phase 3.1 working |
