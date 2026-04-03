#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepL VTT chunk size benchmark.

Usage example:
  python benchmark_deepl_chunks.py input.vtt --key xxx --chunks 40,80,120,160,220
"""

import argparse
import contextlib
import io
import json
import sys
import time
from pathlib import Path

from translate_vtt_zh_deepl_native import read_text, should_translate, translate_lines_native


DEFAULT_CHUNKS = [40, 80, 120, 160, 220]
ALLOWED_ENDPOINTS = {
    "https://api-free.deepl.com/v2/translate",
    "https://api.deepl.com/v2/translate",
}


def parse_chunks(raw: str) -> list[int]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("chunks 不能为空")

    chunks: list[int] = []
    for p in parts:
        value = int(p)
        if value <= 0:
            raise ValueError(f"chunk 必须 > 0: {value}")
        chunks.append(value)
    return chunks


def pick_best(results: list[dict]) -> dict:
    zero_fail = [r for r in results if r["failed_batches"] == 0]
    if zero_fail:
        return min(zero_fail, key=lambda x: x["elapsed_sec"])
    return min(results, key=lambda x: (x["failed_batches"], x["elapsed_sec"]))


def run_once(
    lines: list[str],
    chunk: int,
    api_key: str,
    endpoint: str,
    target_lang: str,
    max_retries: int,
) -> dict:
    failed_batches = 0

    def on_batch_error(_start: int, _end: int, _err: str):
        nonlocal failed_batches
        failed_batches += 1

    total_translatable = sum(1 for line in lines if should_translate(line))
    total_chars = sum(len(line) for line in lines if should_translate(line))

    start = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        _ = translate_lines_native(
            lines,
            api_key=api_key,
            endpoint=endpoint,
            target_lang=target_lang,
            bilingual=False,
            every=max(1, chunk),
            chunk=chunk,
            max_retries=max_retries,
            progress_callback=None,
            stop_check=None,
            batch_error_callback=on_batch_error,
        )
    elapsed = time.perf_counter() - start

    line_per_sec = (total_translatable / elapsed) if elapsed > 0 else 0.0
    char_per_sec = (total_chars / elapsed) if elapsed > 0 else 0.0
    return {
        "chunk": chunk,
        "elapsed_sec": round(elapsed, 3),
        "failed_batches": failed_batches,
        "lines": total_translatable,
        "chars": total_chars,
        "lines_per_sec": round(line_per_sec, 2),
        "chars_per_sec": round(char_per_sec, 2),
    }


def main():
    ap = argparse.ArgumentParser(description="Benchmark DeepL chunk sizes for VTT translation.")
    ap.add_argument("input", help="Path to input .vtt")
    ap.add_argument("--key", required=True, help="DeepL API key")
    ap.add_argument(
        "--endpoint",
        default="https://api-free.deepl.com/v2/translate",
        help="DeepL endpoint",
    )
    ap.add_argument("--target", default="ZH", help="Target language code")
    ap.add_argument(
        "--chunks",
        default=",".join(str(x) for x in DEFAULT_CHUNKS),
        help="Comma-separated chunk sizes, e.g. 40,80,120,160,220",
    )
    ap.add_argument("--max-retries", type=int, default=1, help="Max retries per request")
    ap.add_argument(
        "--report-json",
        default="",
        help="Optional path to write benchmark report in JSON format",
    )
    args = ap.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.exists():
        print(f"ERROR: 输入文件不存在: {in_path}", file=sys.stderr)
        sys.exit(1)

    endpoint = args.endpoint.strip()
    if endpoint not in ALLOWED_ENDPOINTS:
        print("ERROR: endpoint 非法，只允许官方 DeepL Free/Pro 端点", file=sys.stderr)
        sys.exit(1)

    try:
        chunks = parse_chunks(args.chunks)
    except Exception as e:
        print(f"ERROR: 无法解析 chunks: {e}", file=sys.stderr)
        sys.exit(1)

    max_retries = max(0, int(args.max_retries))
    target_lang = args.target.strip().upper()
    api_key = args.key.strip()
    if not api_key:
        print("ERROR: API key 不能为空", file=sys.stderr)
        sys.exit(1)

    lines = read_text(in_path)
    total_translatable = sum(1 for line in lines if should_translate(line))
    if total_translatable == 0:
        print("ERROR: 文件中没有可翻译字幕行", file=sys.stderr)
        sys.exit(1)

    print(f"Benchmark file: {in_path}")
    print(f"Target: {target_lang}, endpoint: {endpoint}")
    print(f"Translatable lines: {total_translatable}")
    print(f"Chunk candidates: {chunks}")
    print("-" * 90)

    results = []
    for idx, chunk in enumerate(chunks, start=1):
        print(f"[{idx}/{len(chunks)}] testing chunk={chunk} ...", flush=True)
        result = run_once(
            lines=lines,
            chunk=chunk,
            api_key=api_key,
            endpoint=endpoint,
            target_lang=target_lang,
            max_retries=max_retries,
        )
        results.append(result)
        print(
            "  -> "
            f"elapsed={result['elapsed_sec']}s, failed_batches={result['failed_batches']}, "
            f"lines/s={result['lines_per_sec']}, chars/s={result['chars_per_sec']}"
        )

    best = pick_best(results)

    print("-" * 90)
    print("Summary:")
    print("chunk | elapsed(s) | failed_batches | lines/s | chars/s")
    for r in results:
        print(
            f"{r['chunk']:>5} | {r['elapsed_sec']:>10} | {r['failed_batches']:>14} | "
            f"{r['lines_per_sec']:>7} | {r['chars_per_sec']:>7}"
        )

    print("-" * 90)
    print(
        "Recommended chunk: "
        f"{best['chunk']} (elapsed={best['elapsed_sec']}s, failed_batches={best['failed_batches']})"
    )

    if args.report_json:
        report_path = Path(args.report_json).expanduser().resolve()
        payload = {
            "input": str(in_path),
            "endpoint": endpoint,
            "target_lang": target_lang,
            "max_retries": max_retries,
            "results": results,
            "recommended": best,
            "created_at_unix": int(time.time()),
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON report written: {report_path}")


if __name__ == "__main__":
    main()
