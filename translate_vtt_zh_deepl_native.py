#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VTT字幕翻译工具 - 使用DeepL原生API

直接调用DeepL API翻译WebVTT字幕文件，支持批量处理和双语输出。
详细使用说明请参考README.md文件。
"""

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Callable, List

import requests

DEEPL_DEFAULT_CHUNK_SIZE = 160
DEEPL_DEFAULT_MAX_RETRIES = 2

TIMECODE_RE = re.compile(r"^\s*\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}")
WEBVTT_RE = re.compile(r"^\s*WEBVTT", re.IGNORECASE)
INDEX_RE = re.compile(r"^\s*\d+\s*$")


def is_timecode(line: str) -> bool:
    return bool(TIMECODE_RE.match(line))


def is_header(line: str) -> bool:
    return bool(WEBVTT_RE.match(line))


def is_index(line: str) -> bool:
    return bool(INDEX_RE.match(line))


def should_translate(line: str) -> bool:
    if not line.strip():
        return False
    if is_header(line) or is_index(line) or is_timecode(line):
        return False
    if line.strip().startswith(("NOTE", "STYLE", "REGION")):
        return False
    return True


def read_text(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=False)
    except UnicodeDecodeError:
        try:
            import chardet

            raw = path.read_bytes()
            enc = chardet.detect(raw).get("encoding") or "utf-8"
            return raw.decode(enc, errors="replace").splitlines(keepends=False)
        except Exception:
            return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=False)


def batch_indices(total: int, batch_size: int):
    start = 0
    while start < total:
        end = min(start + batch_size, total)
        yield start, end
        start = end


def deepl_translate_batch(
    texts: List[str],
    endpoint: str,
    api_key: str,
    target_lang: str,
    formality: str = None,
    max_retries: int = 4,
    base_delay: float = 1.0,
) -> List[str]:
    params = [("text", t) for t in texts]
    data = {
        "target_lang": target_lang,
        "preserve_formatting": "1",
        "split_sentences": "1",
    }
    if formality:
        data["formality"] = formality
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    req_data = params + list(data.items())

    attempt = 0
    while True:
        try:
            resp = requests.post(endpoint, data=req_data, headers=headers, timeout=60)
            if resp.status_code == 200:
                j = resp.json()
                return [item.get("text", "") for item in j.get("translations", [])]
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        except Exception:
            if attempt >= max_retries:
                raise
            attempt += 1
            time.sleep(base_delay * attempt)


def translate_lines_native(
    lines: List[str],
    api_key: str,
    endpoint: str = "https://api-free.deepl.com/v2/translate",
    target_lang: str = "ZH",
    bilingual: bool = False,
    every: int = 10,
    chunk: int = DEEPL_DEFAULT_CHUNK_SIZE,
    max_retries: int = DEEPL_DEFAULT_MAX_RETRIES,
    progress_callback: Callable[[int, int], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
    batch_error_callback: Callable[[int, int, str], None] | None = None,
    log_progress: bool = True,
) -> List[str]:
    translatable_idx = [i for i, ln in enumerate(lines) if should_translate(ln)]
    total = len(translatable_idx)
    out_lines = list(lines)

    for bstart, bend in batch_indices(total, max(1, chunk)):
        if stop_check and not stop_check():
            break

        batch_ids = translatable_idx[bstart:bend]
        batch_texts = [lines[i] for i in batch_ids]
        done_after = bstart + len(batch_ids)
        try:
            translated = deepl_translate_batch(
                batch_texts,
                endpoint=endpoint,
                api_key=api_key,
                target_lang=target_lang,
                max_retries=max_retries,
            )
        except Exception as e:
            print(f"WARNING: DeepL batch failed ({bstart+1}-{bend}): {e}", file=sys.stderr)
            if batch_error_callback:
                batch_error_callback(bstart + 1, bend, str(e))
            translated = batch_texts

        for idx_in_batch, line_idx in enumerate(batch_ids):
            if bilingual:
                out_lines[line_idx] = lines[line_idx] + "\n" + translated[idx_in_batch]
            else:
                out_lines[line_idx] = translated[idx_in_batch]

        if progress_callback:
            progress_callback(done_after, total)
        if log_progress and ((done_after == total) or (done_after % every == 0) or (bstart == 0)):
            print(f"[{done_after}/{total}] Translating...", flush=True)

    return out_lines


def main():
    ap = argparse.ArgumentParser(description="Translate VTT using DeepL native API.")
    ap.add_argument("input", help="Path to input .vtt")
    ap.add_argument("--out", required=True, help="Path to output .vtt")
    ap.add_argument("--key", required=True, help="DeepL API key (Free or Pro)")
    ap.add_argument(
        "--endpoint",
        default="https://api-free.deepl.com/v2/translate",
        help=(
            "DeepL API endpoint (Free: https://api-free.deepl.com/v2/translate, "
            "Pro: https://api.deepl.com/v2/translate)"
        ),
    )
    ap.add_argument("--target", default="ZH", help="Target language code (e.g. ZH / EN / JA)")
    ap.add_argument("--bilingual", action="store_true", help="Keep original + translated line")
    ap.add_argument("--every", type=int, default=10, help="Print progress every N lines")
    ap.add_argument(
        "--chunk",
        type=int,
        default=DEEPL_DEFAULT_CHUNK_SIZE,
        help=f"Number of lines per API request (default: {DEEPL_DEFAULT_CHUNK_SIZE})",
    )
    ap.add_argument(
        "--max-retries",
        type=int,
        default=DEEPL_DEFAULT_MAX_RETRIES,
        help=f"Max retries per request (default: {DEEPL_DEFAULT_MAX_RETRIES})",
    )
    args = ap.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    if not in_path.exists():
        print(f"ERROR: Input not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading: {in_path}")
    lines = read_text(in_path)
    print(f"Translating via DeepL endpoint={args.endpoint} target={args.target} ...")
    failed_batches = 0

    def on_batch_error(_start: int, _end: int, _err: str):
        nonlocal failed_batches
        failed_batches += 1

    out_lines = translate_lines_native(
        lines,
        api_key=args.key,
        endpoint=args.endpoint,
        target_lang=args.target,
        bilingual=args.bilingual,
        every=max(1, args.every),
        chunk=max(1, args.chunk),
        max_retries=max(0, args.max_retries),
        batch_error_callback=on_batch_error,
    )

    print(f"Writing: {out_path}")
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    if failed_batches > 0:
        print(
            f"Done with warnings: {failed_batches} batch(es) failed and kept original text.",
            file=sys.stderr,
        )
    print("Done.")


if __name__ == "__main__":
    main()
