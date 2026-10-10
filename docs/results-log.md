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

## 2026-10-10 — Saturn session: inventory regenerated, ttft.bt first run — TTFT-definition finding

- **Context:** first probe session on saturn (Linux 7.0.0-38-generic, NVIDIA driver 595.99.02,
  llama.cpp 0.6.0-dev built with clang 21.1.8 + CUDA, bpftrace 0.25.0). Server restarted as a
  session service (pid 8945, flags unchanged from 2026-10-09: `127.0.0.1:8080 -ngl 99 -c 4096
  -np 4`, n_ctx_slot 1024, 4 slots; readiness regex `llama_server: listening` works). Page
  caches NOT dropped (weights resident; H3/Phase 4 owns cache state). CUDA graphs: N/A —
  llama.cpp decode path here, no graph capture involved.
- **Runbook 1 done — inventory regenerated:** `sudo ./tools/host-inventory.sh >
  inventory/report.md` (root run, collected 2026-10-10T12:07:04Z). The committed copy
  predated the toolchain install and the script fixes; now current.
- **Runbook 3 done — `ttft.bt` first-ever run** (C=1, 2 requests; repeated once after a probe
  warning fix). Attaches cleanly, one `CONN` line per closed connection, exit histograms on
  SIGINT. All four known risks from the tooling entry resolved:
  1. `tcp_sendmsg` / `tcp_recvmsg` / `tcp_close` all exist (485 `tcp_*` kprobes total).
  2. `delete()` prints "Return value discarded" in **both** spellings — cosmetic; original
     spelling kept, `ttft.bt` header comment corrected. The int64/uint64 sign-mix warning on
     `@bytes` was real noise and is fixed (`(int64)arg2` cast); re-smoked, identical `CONN`
     structure. `bpftrace --dry-run` is a fast traffic-free compile check.
  3. `tcp_close` fires in server context at C=1 — no orphaned map entries observed.
  4. `pid` filter matches the whole server process.
- **FINDING — probe TTFT (`t0 − r0`) measures the HTTP-header write, not the first token.**
  llama-server emits 3 pre-token framing writes and one trailing `[DONE]` write (inferred by
  count — nsends 68 = 3 + 64 tokens + 1; payload not read), so `t0` is a framing write that
  precedes the first token by a variable gap. First validator run against real probe output:
  pairing works (`abs_t0_s` ↔ `r0`, write→read deltas 0.02–0.16 ms, 4/4 pairs across both
  smokes):
  - **TTFT: median abs error 70%** (pairs: 96.3 / 70.5 / 18.3 / 69.5%). Definitional, not
    noise. The Mac fixture missed it — the dummy SSE server sends no headers before the
    first token.
  - **ITL: median abs error 4.3%** (pairs: 11.6 / 4.3 / 3.7 / 4.3%), Pearson r 1.0 in both
    smokes. The strong metric.
  - The mean-ITL framing bias is **mixed-sign**, replacing the fixture's clean −11% model:
    probe mean ITL = (header→token gap + token span + `[DONE]` gap) / (nsends−1), ground
    truth = token span / (ntokens−1). Observed −4.3% when the header gap is ~3–4 ms, +11.6%
    when it is ~35 ms; the sign flips around a ~12 ms header gap (64 tokens, ITL ≈ 2.9 ms).
    The validator's per-pair sends/tokens ratio (1.062) keeps the framing visible.
- **Surprise:** the header write itself is not prompt. `t0 − r0` was 1.34–1.41 ms on three
  connections but **19.8 ms** on the first request after a few minutes idle (that request's
  client TTFT was 24.2 ms, so the header still preceded the first token by only ~4 ms — the
  TTFT error for that pair was coincidentally small, 18.3%). Mechanism unknown (slot
  scheduling? GPU wake?); recorded as-is. Consequence: **no constant correction can turn
  `t0 − r0` into first-token TTFT** — the gap is state-dependent (observed 3–35 ms).
- **Consequence for H2 (blocks the sweep — author decision, runbook step 2):** as currently
  worded, H2's TTFT clause would be refuted by llama.cpp's SSE framing, not by timing
  quality. Options: (a) redefine the clause as time-to-first-byte, add `ttfb_s` to loadgen
  ground truth, probe unchanged; (b) extend the `CONN` line with early-send timestamps
  (t1..t3, last-but-one) so the validator locates the token boundary under the checkable
  per-connection assumption `nsends == n_tokens + 4`; (c) keep as-is and record the limit of
  external observation. Tolerances in `hypotheses.md` remain placeholders until this is
  decided.
- **State left behind:** server running (pid 8945). Next: author decision →
  `sudo ./tools/phase1-sweep.sh` → validate → set H2 status.

## 2026-10-10 — Phase 1 tooling authored on the Mac (no probe work possible here)

- **Context:** bpftrace does not exist on macOS, so nothing below has run against a real
  kernel yet. Everything is authored and checked as far as the Mac allows (`bash -n`,
  `py_compile`, and an end-to-end fixture test of the validator against a dummy SSE server);
  the first smoke happens on saturn.
- **`tools/ttft.bt` — the Phase 1 probe (tests H2).** kprobes on `tcp_recvmsg`,
  `tcp_sendmsg`, `tcp_close`, filtered to the server pid (`$1`). Per-connection state lives
  in maps keyed by `struct sock*`: `@r0` (first read ≈ request arrival), `@t0` (first send),
  `@last`, `@nsends`, `@bytes`. Aggregation stays in-kernel — TTFT/ITL feed `lhist` maps
  (bpftrace 0.25: `lhist(v, min, max, step)`); user space receives **one `CONN` line per
  closed connection** (request granularity, not token granularity) plus the exit
  histograms. Positional args (`arg0`/`arg2`) instead of BTF param names, for
  portability. TTFT_probe = t0 − r0; mean ITL_probe = (last − t0)/(nsends − 1).
- **`tools/phase1-sweep.sh` — attached-vs-detached sweep.** Per concurrency C (default
  1 4 16 64): warmup (unmeasured) → detached baseline → probe attached → identical run →
  SIGINT (exit histograms). Writes `results/phase1/<UTC>/`: `meta.txt` (kernel, driver,
  server cmdline, bpftrace version, git describe, caches-not-dropped statement), per-C
  `detached.jsonl` / `attached.jsonl` / `probe.txt`, and `overhead.md`. Refuses to run
  without root or a live `llama-server`, and verifies the probe actually attached before
  the attached run.
- **`tools/phase1-validate.py` — H2 metrics + probe cost.** `--mode match` pairs each
  ground-truth record with the `CONN` line whose first-read time is nearest the client's
  write time, then reports per-pair TTFT/ITL, median absolute relative error, Pearson r,
  and the send/token ratio. `--mode overhead` prints the attached-vs-detached summary
  delta per concurrency. The H2 tolerances it echoes (10% / 0.9) are placeholders —
  **the author finalizes them before the first measurement.**
- **`tools/loadgen.py` gained one field: `abs_t0_s`** — the absolute CLOCK_MONOTONIC origin
  of each request's timestamps. bpftrace `nsecs` and asyncio `loop.time()` are both
  CLOCK_MONOTONIC-since-boot on the same host, so client records and kernel `CONN` lines
  join without clock conversion. Verified live against a dummy SSE server on the Mac
  (4/4 ok, 32 tokens/request, `abs_t0_s` in the monotonic domain).
- **Send/token bias (affects the H2 error budget):** the Phase 0a smoke saw 288 server
  sends vs 256 client tokens (≈1.125 sends/token — SSE framing, `data: [DONE]`, headers).
  Per-connection *mean* inter-send time is therefore biased low by roughly
  (n_tokens−1)/(n_sends−1) ≈ 0.89 before any measurement error. The validator reports the
  per-pair ratio so this stays visible; the fixture test reproduces it exactly (11.4% ITL
  error at ratio 1.125, TTFT error 2.0%).
- **Date-label convention fixed:** log dates are the authoring Mac's local time (UTC−5).
  The former 2026-10-10 labels were UTC artifacts (inventory collected
  2026-10-10T03:26:40Z = 2026-10-09 22:26 local, matching git) and broke this file's
  newest-first order. Relabeled here and in `docs/environment.md`; `inventory/report.md`
  keeps its explicit UTC timestamp.
- **Known risks for the first smoke (saturn):** (1) positional args assume
  `tcp_sendmsg(sk, msg, size)` / `tcp_recvmsg(sk, …)` / `tcp_close(sk, …)` — verify with
  `sudo bpftrace -l 'kprobe:tcp_*'`; `tcp_close` was not in the inventory's kallsyms
  check. (2) `delete(@m[k])` may print a deprecation warning on 0.25 — cosmetic;
  `delete(@m, k)` is the newer spelling if it ever hard-errors. (3) `CONN` lines only
  print when `tcp_close` runs in server context — RST-orphaned sockets would leak map
  entries (harmless within a run; fresh bpftrace per sweep step). (4) `pid` builtin is
  tgid — all server threads match, intended.

### Saturn runbook (supersedes the list in the 2026-10-09 entry below)

1. **Regenerate the committed inventory** — still pending; it predates the toolchain
   install and the script fixes:
   `sudo ./tools/host-inventory.sh > inventory/report.md`, commit, push.
2. **Author decision (blocks measurement):** finalize H2 tolerances in
   `docs/hypotheses.md` — 10% median-error / 0.9 correlation are flagged placeholders;
   decide with the send/token bias above in view.
3. **Smoke the probe** (first-ever run of `ttft.bt`): verify symbols, then
   `sudo bpftrace tools/ttft.bt $(pgrep -x llama-server) | tee /tmp/probe-smoke.txt`
   alongside one `tools/loadgen.py --concurrency 1 --requests 2 --out /tmp/smoke.jsonl`;
   expect ~2 `CONN` lines + exit histograms.
4. **Run the sweep:** `sudo ./tools/phase1-sweep.sh` (defaults 1 4 16 64; requests per C
   default max(16, 4C)). Outputs are root-owned.
5. **Validate:** per C, `python3 tools/phase1-validate.py --mode match --probe
   results/phase1/<ts>/c$C/probe.txt --ground-truth .../c$C/attached.jsonl`; read
   `overhead.md` — **quote no eBPF-derived number without it**.
6. **Log + decide:** results entry here (newest first; kernel/driver/server versions,
   caches-not-dropped statement, CUDA-graphs state), set H2 status in
   `docs/hypotheses.md`, commit `results/`.

- **Deliberately not done (author calls):** `bcc-tools` install (Phase 2), vLLM
  introduction, any change to the server binary or its flags.

## 2026-10-09 — Doc/code cleanup from Mac (no probe work)

- **Reviewed the repo from the authoring Mac** (cannot run probes here). Fixed four
  inconsistencies found during review:
  1. **AGENTS.md status was stale** — still said "Phase 0a, no experiments run" with the
     inventory as the immediate task, contradicting README and this log. Updated to reflect
     current state: inventory done, blockers cleared, server up, probe-attach goal met, next
     milestone is Phase 1.
  2. **`tools/host-inventory.sh` lockdown verdict was self-contradictory** — the `case` matched
     the literal `[none]` against raw `/sys/kernel/security/lockdown` output like
     `none [integrity] confidentiality`, so it never matched and fell through to "kprobes may be
     blocked" as a hard claim, contradicting the empirical proof that kprobes fire. Now parses
     the bracketed (active) token; the verdict for `integrity`/`confidentiality` says "may be
     blocked (verify empirically)" — honest, consistent with the empirical-first ethos.
  3. **`tools/host-inventory.sh` virtualisation row had a newline** — `systemd-detect-virt`
     outputs `none` but exits non-zero, so `|| echo 'not systemd / unknown'` appended, breaking
     the markdown table cell. Fixed to capture first, fallback on empty.
  4. **`tools/loadgen.py` used deprecated `asyncio.get_event_loop()`** (5 sites) — replaced
     with `asyncio.get_running_loop()`. No behavior change; removes the deprecation warning on
     the host's Python 3.14.4.
- **Flagged in `docs/environment.md`:** `bcc-tools` is still absent (0 tools), blocking
  Phase 2's `offcputime`/`profile` BCC tools. Install before Phase 2.

### Next steps on saturn

Superseded by the runbook in the 2026-10-10 entry above. Still-pending author actions:
regenerate `inventory/report.md` (the committed copy predates the toolchain install and
the script fixes) and finalize the H2 tolerances in `docs/hypotheses.md`.

## 2026-10-09 — Host inventory + first probe attach (Phase 0a start)

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
- **Toolchain installed (author-approved 2026-10-09):** gcc 15.2, clang 21.1.8, cmake 4.2.3,
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
