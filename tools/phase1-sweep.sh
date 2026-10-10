#!/usr/bin/env bash
# Phase 1 sweep (tests H2): loadgen at several concurrency levels, probe
# attached vs detached — so the probe's own cost is measured before any
# eBPF-derived number is quoted (docs/design.md cross-cutting requirement).
#
# For each concurrency C: warmup (unmeasured) -> detached baseline run ->
# probe attached -> identical run -> SIGINT probe (prints exit histograms).
#
# Output under $OUTDIR (default results/phase1/<UTC stamp>):
#   meta.txt              kernel / driver / server cmdline / bpftrace / git,
#                        run parameters, page-cache statement
#   c$C/warmup.jsonl     warmup requests (not measured)
#   c$C/detached.jsonl   ground truth, probe off   } compare: probe cost
#   c$C/attached.jsonl   ground truth, probe on    } (overhead.md)
#   c$C/probe.txt        ttft.bt output: CONN lines + TTFT/ITL lhist at exit
#   overhead.md          per-C attached-vs-detached summary deltas
#
# Usage:  sudo ./tools/phase1-sweep.sh [concurrency ...]    # default: 1 4 16 64
# Env:    OUTDIR, MAX_TOKENS (32), PROMPT, HOST (127.0.0.1), PORT (8080),
#         REQUESTS (default max(16, 4*C) — scaled per C, recorded in meta.txt)
# Needs root (bpftrace) and a running llama-server. Run from anywhere; this
# cds to its own repo root.

set -euo pipefail

cd "$(dirname "$0")/.."

HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8080}
MAX_TOKENS=${MAX_TOKENS:-32}
PROMPT=${PROMPT:-}
OUTDIR=${OUTDIR:-results/phase1/$(date -u +%Y%m%dT%H%M%SZ)}

conns=("$@")
if [ ${#conns[@]} -eq 0 ]; then conns=(1 4 16 64); fi

if [ "$(id -u)" -ne 0 ]; then
	echo "error: needs root for bpftrace — run with sudo" >&2
	exit 1
fi

PID="$(pgrep -x llama-server | head -n1 || true)"
if [ -z "$PID" ]; then
	echo "error: llama-server is not running" >&2
	exit 1
fi
curl -fsS "http://$HOST:$PORT/health" >/dev/null \
	|| { echo "error: http://$HOST:$PORT/health is not healthy" >&2; exit 1; }

mkdir -p "$OUTDIR"
{
	echo "date_utc:           $(date -u +%Y-%m-%dT%H:%M:%SZ)"
	echo "kernel:             $(uname -r)"
	echo "bpftrace:           $(bpftrace --version 2>&1 | head -n1)"
	echo "server_pid:         $PID"
	echo "server_cmd:         $(tr '\0' ' ' </proc/$PID/cmdline)"
	echo "gpu:                $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null || echo n/a)"
	echo "git:                $(git describe --always --dirty 2>/dev/null || echo unknown)"
	echo "conns:              ${conns[*]}"
	echo "requests_per_conn:  max(16, 4*C) unless REQUESTS set"
	echo "max_tokens:         $MAX_TOKENS"
	echo "prompt:             ${PROMPT:-<loadgen default>}"
	echo "page_caches_dropped: no (weights resident after warmup; H3/Phase 4 controls this)"
} >"$OUTDIR/meta.txt"

run_loadgen() { # run_loadgen <concurrency> <requests> <out.jsonl>
	local c=$1 n=$2 out=$3
	if [ -n "$PROMPT" ]; then
		python3 tools/loadgen.py --host "$HOST" --port "$PORT" \
			--concurrency "$c" --requests "$n" --max-tokens "$MAX_TOKENS" \
			--prompt "$PROMPT" --out "$out"
	else
		python3 tools/loadgen.py --host "$HOST" --port "$PORT" \
			--concurrency "$c" --requests "$n" --max-tokens "$MAX_TOKENS" \
			--out "$out"
	fi
}

echo "# Probe overhead — attached vs detached (quote no eBPF number without this)" \
	>"$OUTDIR/overhead.md"

for C in "${conns[@]}"; do
	if [ -n "${REQUESTS:-}" ]; then
		n=$REQUESTS
	else
		n=$((C * 4))
		if [ "$n" -lt 16 ]; then n=16; fi
	fi
	d="$OUTDIR/c$C"
	mkdir -p "$d"
	echo "== c=$C requests=$n =="

	# 1. warmup — primes weights/pages; not measured
	run_loadgen 1 2 "$d/warmup.jsonl" >/dev/null

	# 2. detached baseline — probe off
	run_loadgen "$C" "$n" "$d/detached.jsonl" >/dev/null

	# 3. attached run — probe on; SIGINT prints the exit histograms
	bpftrace tools/ttft.bt "$PID" >"$d/probe.txt" 2>&1 &
	BPID=$!
	sleep 5
	if ! kill -0 "$BPID" 2>/dev/null || ! grep -q Attaching "$d/probe.txt"; then
		echo "error: bpftrace did not attach — see $d/probe.txt" >&2
		kill "$BPID" 2>/dev/null || true
		exit 1
	fi
	run_loadgen "$C" "$n" "$d/attached.jsonl" >/dev/null
	kill -INT "$BPID" 2>/dev/null || true
	wait "$BPID" 2>/dev/null || true

	# 4. probe cost at this concurrency
	python3 tools/phase1-validate.py --mode overhead \
		--detached "$d/detached.jsonl" --attached "$d/attached.jsonl" \
		--title "c=$C" >>"$OUTDIR/overhead.md"
	echo >>"$OUTDIR/overhead.md"
done

echo
echo "sweep complete -> $OUTDIR"
echo "next: for each C, run"
echo "  python3 tools/phase1-validate.py --mode match \\"
echo "    --probe $OUTDIR/c<C>/probe.txt --ground-truth $OUTDIR/c<C>/attached.jsonl"