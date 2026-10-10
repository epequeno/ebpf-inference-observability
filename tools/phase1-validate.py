#!/usr/bin/env python3
"""Validate H2 (Phase 1.3): eBPF-derived TTFT/ITL vs client ground truth.

Modes
  match     Pair tools/ttft.bt per-connection CONN lines with tools/loadgen.py
            records from the SAME attached run; report per-pair TTFT/ITL,
            median absolute relative error, Pearson correlation, and the
            send/token ratio (see bias note below).
  overhead  Compare run summaries with the probe detached vs attached — the
            probe's own cost. docs/design.md: quote no eBPF-derived number
            without this comparison.

Clocks: bpftrace nsecs and asyncio loop.time() are both CLOCK_MONOTONIC
since boot on the same host, so client records (abs_t0_s, new in loadgen)
and kernel CONN lines join without conversion — only differences are used.

Send/token bias: SSE framing means more sends than tokens (Phase 0a smoke:
288 sends vs 256 tokens ≈ 1.125). Per-connection MEAN inter-send time is
therefore biased low by roughly (n_tokens-1)/(n_sends-1). The per-pair
sends/tokens ratio is reported so this stays visible, not silently absorbed.

H2 tolerances (docs/hypotheses.md: median abs error 10%, correlation 0.9)
are PLACEHOLDERS for the author to finalize BEFORE the first measurement.
This tool reports numbers; it does not decide pass/fail.

Usage:
  phase1-validate.py --mode match --probe probe.txt --ground-truth attached.jsonl [--slack 10]
  phase1-validate.py --mode overhead --detached detached.jsonl --attached attached.jsonl [--title c=4]
"""

import argparse
import json
import re
import statistics

# CONN <sk> <r0> <t0> <last> <nsends> <bytes> — times in nsecs
CONN_RE = re.compile(r"CONN (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)")


def load_probe(path):
    """Probe CONN lines -> [{sk, r0, t0, last, nsends, bytes}] (seconds)."""
    conns = []
    with open(path) as fh:
        for line in fh:
            m = CONN_RE.search(line)
            if not m:
                continue
            sk, r0, t0, last, nsends, nbytes = (int(x) for x in m.groups())
            if t0 == 0 or nsends == 0:
                continue  # connection that never sent
            conns.append(
                {
                    "sk": sk,
                    "r0": r0 / 1e9,
                    "t0": t0 / 1e9,
                    "last": last / 1e9,
                    "nsends": nsends,
                    "bytes": nbytes,
                }
            )
    return conns


def load_records(path):
    """Ground-truth JSONL -> ok records with abs_t0_s and ttft_s."""
    recs, stale = [], 0
    with open(path) as fh:
        for line in fh:
            obj = json.loads(line)
            if "summary" in obj:
                continue
            if not obj.get("ok"):
                continue
            if obj.get("abs_t0_s") is None:
                stale += 1
                continue
            if obj.get("ttft_s") is None:
                continue
            recs.append(obj)
    if stale:
        print(f"warning: {stale} ok records lack abs_t0_s "
              "(old loadgen?) — skipped", flush=True)
    return recs


def pair(conns, recs, slack):
    """Greedy nearest-time pairing: client write time vs server first read."""
    pairs, unmatched = [], []
    used = [False] * len(conns)
    for r in sorted(recs, key=lambda r: r["abs_t0_s"] + r["sent_at"]):
        write = r["abs_t0_s"] + r["sent_at"]
        best, best_d = None, None
        for i, c in enumerate(conns):
            if used[i]:
                continue
            d = abs(c["r0"] - write)
            if best_d is None or d < best_d:
                best, best_d = i, d
        if best is not None and best_d <= slack:
            used[best] = True
            pairs.append((r, conns[best]))
        else:
            unmatched.append(r)
    return pairs, unmatched


def corr(xs, ys):
    if len(xs) < 2:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None  # e.g. zero variance


def pct(xs):
    return f"{statistics.median(xs) * 100:.1f}%"


def mode_match(args):
    conns = load_probe(args.probe)
    recs = load_records(args.ground_truth)
    pairs, unmatched = pair(conns, recs, args.slack)

    tt_err, tt_probe, tt_gt = [], [], []
    itl_err, itl_probe, itl_gt = [], [], []
    ratios = []

    print("| idx | ttft gt s | ttft probe s | err | itl gt s | itl probe s | err | sends | tokens | sends/tokens |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r, c in pairs:
        tp = c["t0"] - c["r0"]
        tg = r["ttft_s"]
        ip = (c["last"] - c["t0"]) / (c["nsends"] - 1) if c["nsends"] > 1 else None
        ig = statistics.fmean(r["itl_s"]) if r["itl_s"] else None
        e_tt = abs(tp - tg) / tg if tg else None
        e_itl = abs(ip - ig) / ig if ip is not None and ig else None
        ratio = c["nsends"] / r["n_tokens"] if r["n_tokens"] else None
        fmt = lambda v: f"{v:.4f}" if v is not None else "n/a"
        ferr = lambda v: f"{v * 100:.1f}%" if v is not None else "n/a"
        print(f"| {r['index']} | {fmt(tg)} | {fmt(tp)} | {ferr(e_tt)} | "
              f"{fmt(ig)} | {fmt(ip)} | {ferr(e_itl)} | {c['nsends']} | "
              f"{r['n_tokens']} | {fmt(ratio) if ratio else 'n/a'} |")
        if e_tt is not None:
            tt_err.append(e_tt)
            tt_probe.append(tp)
            tt_gt.append(tg)
        if e_itl is not None:
            itl_err.append(e_itl)
            itl_probe.append(ip)
            itl_gt.append(ig)
        if ratio:
            ratios.append(ratio)

    print()
    print(f"pairs: {len(pairs)}/{len(recs)} ground-truth records "
          f"({len(unmatched)} unmatched, {len(conns) - len(pairs)} unpaired probe conns, "
          f"slack {args.slack}s)")
    if tt_err:
        r = corr(tt_gt, tt_probe)
        print(f"ttft: median abs rel error {pct(tt_err)}  "
              f"pearson r {r:.4f}" if r is not None else f"ttft: median abs rel error {pct(tt_err)}  pearson r n/a")
    else:
        print("ttft: no pairs")
    if itl_err:
        r = corr(itl_gt, itl_probe)
        print(f"itl:  median abs rel error {pct(itl_err)}  "
              f"pearson r {r:.4f}" if r is not None else f"itl:  median abs rel error {pct(itl_err)}  pearson r n/a")
    else:
        print("itl:  no pairs")
    if ratios:
        print(f"send/token ratio: median {statistics.median(ratios):.3f} "
              f"— biases probe mean-ITL low by ~(tokens-1)/(sends-1)")
    print()
    print("H2 placeholders (docs/hypotheses.md; author must finalize BEFORE "
          "the first measurement): median abs error <= 10%, correlation >= 0.9")


def load_summary(path):
    with open(path) as fh:
        for line in fh:
            obj = json.loads(line)
            if "summary" in obj:
                return obj["summary"]
    return {}


def mode_overhead(args):
    det = load_summary(args.detached)
    att = load_summary(args.attached)
    print(f"### {args.title or 'probe cost'}")
    print()
    print("| metric | detached | attached | delta |")
    print("|---|---|---|---|")
    for key, label in [
        ("ttft_s_p50", "ttft p50 (s)"),
        ("ttft_s_p90", "ttft p90 (s)"),
        ("itl_s_mean", "itl mean (s)"),
        ("itl_s_p50", "itl p50 (s)"),
        ("tokens_total", "tokens"),
    ]:
        dv, av = det.get(key), att.get(key)
        if isinstance(dv, (int, float)) and isinstance(av, (int, float)) and dv:
            delta = f"{(av - dv) / dv * 100:+.1f}%"
        else:
            delta = "n/a"
        print(f"| {label} | {dv} | {av} | {delta} |")
    print(f"\nok: {det.get('ok')} -> {att.get('ok')}  "
          f"failed: {det.get('failed')} -> {att.get('failed')}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", required=True, choices=["match", "overhead"])
    p.add_argument("--probe", help="ttft.bt output (match mode)")
    p.add_argument("--ground-truth", help="loadgen JSONL from the same attached run (match) or detached run (overhead)")
    p.add_argument("--slack", type=float, default=10.0,
                   help="max |server first read - client write| for pairing (s)")
    p.add_argument("--detached", help="detached-run JSONL (overhead mode)")
    p.add_argument("--attached", help="attached-run JSONL (overhead mode)")
    p.add_argument("--title", help="table title (overhead mode)")
    args = p.parse_args()

    if args.mode == "match":
        if not args.probe or not args.ground_truth:
            p.error("--mode match needs --probe and --ground-truth")
        mode_match(args)
    else:
        if not args.detached or not args.attached:
            p.error("--mode overhead needs --detached and --attached")
        mode_overhead(args)


if __name__ == "__main__":
    main()