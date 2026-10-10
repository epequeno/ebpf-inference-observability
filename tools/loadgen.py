#!/usr/bin/env python3
"""Synthetic OpenAI-compatible streaming load for the server under observation.

Records client-side ground truth for H2 (Phase 1.3): TTFT = time from request off
the socket to first token at the socket read; ITL = inter-token arrival gaps;
ttfb_s = first response byte of any kind (the headers — the client-side mirror
of the probe's first-send timestamp); end_s = arrival of 'data: [DONE]' (or
stream end) — together they form the framing-symmetric response span that
tools/phase1-validate.py compares against the probe's (t0, last) span.

The design requires ground truth be measured at the client's *socket read*, not at
the HTTP library's iterator (H2 caveat). So we bypass httpx/aiohttp and read the
raw HTTP response ourselves via asyncio streams.

Each record also carries abs_t0_s: the absolute CLOCK_MONOTONIC timestamp of
that request's clock origin, so records join to kernel-side timestamps (bpftrace
nsecs — same clock since boot). Used by tools/phase1-validate.py.

Usage:
    python3 tools/loadgen.py --concurrency 4 --requests 32 --max-tokens 64 \\
        --out /tmp/ground-truth.jsonl --prompt "Explain eBPF in one paragraph."
"""

import argparse
import asyncio
import json
import statistics

HOST = "127.0.0.1"
PORT = 8080


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--url", default=None, help="full endpoint URL, overrides host/port (default http://host:port/completion)")
    p.add_argument("--concurrency", type=int, default=1, help="simultaneous in-flight requests")
    p.add_argument("--requests", type=int, default=16, help="total requests to send")
    p.add_argument("--prompt", default="Explain how eBPF works, briefly.")
    p.add_argument("--max-tokens", type=int, default=64)
    p.add_argument("--timeout", type=float, default=120.0, help="per-request wall-clock timeout (s)")
    p.add_argument("--gap", type=float, default=0.0, help="if >0, request i starts i*gap seconds after t0 (bursty arrival pacing; H4)")
    p.add_argument("--out", required=True, help="JSONL file: one ground-truth record per request plus a summary")
    p.add_argument("--api", choices=["llamacpp", "openai"], default="llamacpp",
                   help="response dialect: llama.cpp /completion or OpenAI /v1/completions")
    p.add_argument("--model", default=None,
                   help="model id for --api openai payloads (e.g. Qwen/Qwen2.5-1.5B-Instruct)")
    return p.parse_args()


def make_payload(args, prompt, max_tokens):
    payload = {"prompt": prompt, "max_tokens": max_tokens, "stream": True}
    if args.api == "openai":
        payload["model"] = args.model or "default"
    return payload


class TokenTimedOut(Exception):
    pass


async def read_streaming_response(reader, timeout, sock_start, api):
    """Socket-level SSE reader. Returns (first_token, [token...], first_byte, done).

    Timestamps are monotonic seconds since sock_start. first_byte is the first
    line of ANY kind — the response headers for SSE, i.e. the client-side mirror
    of the probe's first-send timestamp; done is the arrival of 'data: [DONE]'
    (or stream end if the server never sends it). 'data: [DONE]' ends the
    stream; the server may also end with a single JSON line (non-stream
    fallback).
    """
    first = None
    tokens = []
    first_byte = None
    done = None
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TokenTimedOut(f"timed out after {timeout}s ({len(tokens)} tokens)")
        line = await asyncio.wait_for(reader.readline(), remaining)
        if not line:
            break
        now = asyncio.get_running_loop().time() - sock_start
        if first_byte is None:
            first_byte = now
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith("data:"):
            continue
        event = text[5:].strip()
        if event == "[DONE]":
            done = now
            break
        try:
            obj = json.loads(event)
        except json.JSONDecodeError:
            continue
        if api == "openai":
            piece = (obj.get("choices") or [{}])[0].get("text", "")
        else:
            piece = obj.get("content", "")
        if not piece:
            continue
        if first is None:
            first = now
        tokens.append(now)
    if done is None:
        done = asyncio.get_running_loop().time() - sock_start
    return first, tokens, first_byte, done


async def one_request(args, idx):
    try:
        r, w = await asyncio.wait_for(
            asyncio.open_connection(args.host, args.port), timeout=args.timeout
        )
    except (OSError, asyncio.TimeoutError) as exc:
        return {"index": idx, "error": f"connect: {exc!r}", "ok": False}

    body = json.dumps(make_payload(args, args.prompt, args.max_tokens)).encode()
    sock_start = asyncio.get_running_loop().time()
    req = (
        b"POST " + args.path.encode() + b" HTTP/1.1\r\n"
        b"Host: " + args.host.encode() + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n"
    )
    try:
        w.write(req + body)
        await w.drain()
        sent_at = asyncio.get_running_loop().time() - sock_start
        first, tokens, first_byte, done = await read_streaming_response(r, args.timeout, sock_start, args.api)
        w.close()
        await w.wait_closed()
    except (OSError, asyncio.TimeoutError, TokenTimedOut) as exc:
        return {"index": idx, "error": f"stream: {exc!r}", "ok": False}

    ttft = first if first is not None else None
    itls = [b - a for a, b in zip(tokens, tokens[1:])] if tokens else []
    return {
        "index": idx,
        "ok": True,
        "sent_at": sent_at,
        "abs_t0_s": sock_start,  # CLOCK_MONOTONIC origin of this record's times;
                                 # joins to kernel bpftrace nsecs (same clock)
        "ttft_s": ttft,
        "ttfb_s": first_byte,  # first response byte (headers), rel. to abs_t0_s
        "end_s": done,         # 'data: [DONE]' (or stream end), rel. to abs_t0_s
        "n_tokens": len(tokens),
        "itl_s": itls,
        "duration_s": (tokens[-1] if tokens else None),
    }


async def run(args):
    sem = asyncio.Semaphore(args.concurrency)
    results = []

    async def worker(i):
        if args.gap > 0:
            # arrival pacing: request i starts args.gap after request i-1, exact
            # spacing regardless of how long requests take (H4 bursty pattern)
            await asyncio.sleep(i * args.gap)
        async with sem:
            results.append(await one_request(args, i))

    await asyncio.gather(*(worker(i) for i in range(args.requests)))
    return results


def summary(records):
    ok = [r for r in records if r.get("ok")]
    failed = [r for r in records if not r.get("ok")]
    ttft = [r["ttft_s"] for r in ok if r["ttft_s"] is not None]
    itl = [x for r in ok for x in r["itl_s"]]
    ttfb = [r["ttfb_s"] for r in ok if r.get("ttfb_s") is not None]

    def pct(vals, q):
        return round(statistics.quantiles(sorted(vals), n=20)[int(q * 20) - 1], 4) if vals else None

    return {
        "ok": len(ok),
        "failed": len(failed),
        "ttft_s_p50": pct(ttft, 0.5),
        "ttft_s_p90": pct(ttft, 0.9),
        "ttfb_s_p50": pct(ttfb, 0.5),
        "itl_s_mean": round(statistics.mean(itl), 4) if itl else None,
        "itl_s_p50": pct(itl, 0.5),
        "tokens_total": sum(r["n_tokens"] for r in ok),
    }


def main():
    args = parse_args()
    if args.url:
        from urllib.parse import urlsplit
        u = urlsplit(args.url)
        args.host, args.port, args.path = u.hostname, u.port or 80, u.path or "/completion"
    else:
        args.path = "/completion"

    records = asyncio.run(run(args))
    with open(args.out, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
        fh.write(json.dumps({"summary": summary(records)}) + "\n")
    print(json.dumps(summary(records), indent=2))


if __name__ == "__main__":
    main()