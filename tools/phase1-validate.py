#!/usr/bin/env python3
"""Validate H2 (Phase 1.3): eBPF-derived TTFT/ITL vs client ground truth.

Modes
  match     Pair tools/ttft.bt per-connection CONN lines with tools/loadgen.py
            records from the SAME attached run; report per-pair TTFB and
            framing-symmetric ITL, median absolute relative error, Pearson
            correlation, and the send/token ratio (see framing note below).
            Raw, framing-uncorrected references are reported alongside.
  overhead  Compare run summaries with the probe detached vs attached — the
            probe's own cost. docs/design.md: quote no eBPF-derived number
            without this comparison.

Clocks: bpftrace nsecs and asyncio loop.time() are both CLOCK_MONOTONIC
since boot on the same host, so client records (abs_t0_s, new in loadgen)
and kernel CONN lines join without conversion — only differences are used.

Framing (measured, saturn 2026-10-10): llama.cpp emits ~3 pre-token header
writes and one trailing [DONE] write per connection (sends/tokens ≈ 1.06 at
64 tokens), so probe (t0 - r0) is TTFB, not first-token TTFT, and the raw
mean inter-send time is framing-biased with MIXED sign (-4% to +12%
observed at C=1). The H2 metrics are therefore TTFB and the
framing-symmetric ITL: both sides measure the same physical span
(first send/byte -> last send/byte) over the same send count. The per-pair
sends/tokens ratio is reported so framing stays visible, never silently
absorbed.

TTFB is compared same-interval: the client's origin is the probe's own r0
(author ruling 2026-10-10, recorded after the sweep; both clocks join, so
this is computable from the same collected data). The conventional
write-origin number is reported alongside as informational.

H2 tolerances were finalized 2026-10-10 BEFORE the first measurement
(docs/hypotheses.md): corrected TTFB and framing-symmetric ITL must meet
median abs rel error <= 10% and Pearson r >= 0.9. This tool reports
numbers; it does not decide pass/fail.

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
            if obj.get("abs_t0_s") is None or obj.get("ttfb_s") is None \
                    or obj.get("end_s") is None:
                stale += 1
                continue
            if obj.get("ttft_s") is None:
                continue
            recs.append(obj)
    if stale:
        print(f"warning: {stale} ok records lack abs_t0_s/ttfb_s/end_s "
              "(old loadgen output?) — skipped", flush=True)
    return recs


def pair(conns, recs, slack):
    """Greedy nearest-time pairing, cross-checked on BOTH connection endpoints.

    Candidate by client-write vs server-first-read (r0); accepted only if the
    client's stream end also matches the connection's last send. Under a
    concurrent burst every write and every r0 land within microseconds of each
    other, so read-proximity alone misassigns records across queueing waves —
    observed at C>=16 as impossible pairs (client TTFB < probe TTFB). The
    end-time check rejects cross-wave swaps (they differ by a whole request
    duration); same-wave swaps it cannot see, but their metrics are
    near-identical, so the error impact is nil.
    """
    END_SLACK = 0.05  # s; true pairs differ by loopback transit (~us-ms)
    pairs, unmatched = [], []
    used = [False] * len(conns)
    for r in sorted(recs, key=lambda r: r["abs_t0_s"] + r["sent_at"]):
        write = r["abs_t0_s"] + r["sent_at"]
        end = r["abs_t0_s"] + r["end_s"]
        cands = sorted(
            (abs(c["r0"] - write), i) for i, c in enumerate(conns) if not used[i]
        )
        pick = None
        for d, i in cands:
            if d > slack:
                break
            if abs(conns[i]["last"] - end) <= END_SLACK:
                pick = i
                break
        if pick is not None:
            used[pick] = True
            pairs.append((r, conns[pick]))
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

    ttfb_err, ttfb_probe, ttfb_gt = [], [], []
    ttfb_w_err = []  # conventional write-origin TTFB (informational)
    itl_err, itl_probe, itl_gt = [], [], []  # framing-symmetric
    ttft_raw_err, itl_raw_err = [], []
    ratios = []

    print("| idx | ttfb gt s | ttfb probe s | err | itl-fs gt s | itl-fs probe s | err | sends | tokens | ratio |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r, c in pairs:
        write = r["abs_t0_s"] + r["sent_at"]
        fb_abs = r["abs_t0_s"] + r["ttfb_s"]   # first response byte
        end_abs = r["abs_t0_s"] + r["end_s"]  # [DONE] / stream end
        tp = c["t0"] - c["r0"]                # probe TTFB: read -> header write
        tg = fb_abs - c["r0"]                 # client TTFB, same interval (author
                                             # ruling 2026-10-10): server read -> first byte
        ip = (c["last"] - c["t0"]) / (c["nsends"] - 1) if c["nsends"] > 1 else None
        ig = (end_abs - fb_abs) / (c["nsends"] - 1) if c["nsends"] > 1 else None
        e_ttfb = abs(tp - tg) / tg if tg else None
        e_ttfb_w = abs(tp - (fb_abs - write)) / (fb_abs - write) if fb_abs > write else None
        e_itl = abs(ip - ig) / ig if ip is not None and ig else None
        # raw, framing-uncorrected references (reported per the finalized H2 note)
        e_ttft_raw = abs(tp - r["ttft_s"]) / r["ttft_s"] if r["ttft_s"] else None
        ig_tok = statistics.fmean(r["itl_s"]) if r["itl_s"] else None
        e_itl_raw = abs(ip - ig_tok) / ig_tok if ip is not None and ig_tok else None
        ratio = c["nsends"] / r["n_tokens"] if r["n_tokens"] else None
        fmt = lambda v: f"{v:.4f}" if v is not None else "n/a"
        ferr = lambda v: f"{v * 100:.1f}%" if v is not None else "n/a"
        print(f"| {r['index']} | {fmt(tg)} | {fmt(tp)} | {ferr(e_ttfb)} | "
              f"{fmt(ig)} | {fmt(ip)} | {ferr(e_itl)} | {c['nsends']} | "
              f"{r['n_tokens']} | {fmt(ratio) if ratio else 'n/a'} |")
        if e_ttfb is not None:
            ttfb_err.append(e_ttfb)
            ttfb_probe.append(tp)
            ttfb_gt.append(tg)
        if e_ttfb_w is not None:
            ttfb_w_err.append(e_ttfb_w)
        if e_itl is not None:
            itl_err.append(e_itl)
            itl_probe.append(ip)
            itl_gt.append(ig)
        if e_ttft_raw is not None:
            ttft_raw_err.append(e_ttft_raw)
        if e_itl_raw is not None:
            itl_raw_err.append(e_itl_raw)
        if ratio:
            ratios.append(ratio)

    print()
    print(f"pairs: {len(pairs)}/{len(recs)} ground-truth records "
          f"({len(unmatched)} unmatched, {len(conns) - len(pairs)} unpaired probe conns, "
          f"slack {args.slack}s)")
    if ttfb_err:
        r = corr(ttfb_gt, ttfb_probe)
        print(f"ttfb same-interval (H2 metric): median abs rel error {pct(ttfb_err)}  "
              + (f"pearson r {r:.4f}" if r is not None else "pearson r n/a"))
    else:
        print("ttfb: no pairs")
    if ttfb_w_err:
        print(f"ttfb write-origin (conventional TTFB; informational): "
              f"median abs rel error {pct(ttfb_w_err)}")
    if itl_err:
        r = corr(itl_gt, itl_probe)
        print(f"itl framing-symmetric (H2 metric): median abs rel error {pct(itl_err)}  "
              + (f"pearson r {r:.4f}" if r is not None else "pearson r n/a"))
    else:
        print("itl: no pairs")
    if ttft_raw_err:
        print(f"ttft raw (probe t0-r0 vs client first-token; informational): "
              f"median abs rel error {pct(ttft_raw_err)}")
    if itl_raw_err:
        print(f"itl raw (vs client token-mean; informational): "
              f"median abs rel error {pct(itl_raw_err)}")
    if ratios:
        print(f"send/token ratio: median {statistics.median(ratios):.3f} "
              f"— the framing structure behind the raw-vs-corrected gap")
    print()
    print("H2 criteria (finalized 2026-10-10, docs/hypotheses.md): ttfb (same-interval, "
          "author ruling 2026-10-10) and framing-symmetric itl — median abs rel error <= 10%, "
          "pearson r >= 0.9; conventional/raw values reported alongside.")


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
        ("ttfb_s_p50", "ttfb p50 (s)"),
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