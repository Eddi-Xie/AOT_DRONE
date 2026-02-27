#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Push a JPEG frame to backend /api/frame")
    parser.add_argument("jpeg_path", type=pathlib.Path, help="Path to a JPEG file")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/api/frame",
        help="Frame ingest URL (default: http://127.0.0.1:8000/api/frame)",
    )
    parser.add_argument("--timeout-s", type=float, default=3.0, help="HTTP timeout seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.jpeg_path.exists() or not args.jpeg_path.is_file():
        print(f"error: file not found: {args.jpeg_path}", file=sys.stderr)
        return 2

    payload = args.jpeg_path.read_bytes()
    request = urllib.request.Request(
        args.url,
        data=payload,
        method="POST",
        headers={"Content-Type": "image/jpeg"},
    )

    try:
        with urllib.request.urlopen(request, timeout=args.timeout_s) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            print(f"status={response.status}")
            print(response_body)
            return 0 if response.status == 200 else 1
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"status={exc.code}", file=sys.stderr)
        try:
            parsed = json.loads(body)
            print(json.dumps(parsed, indent=2), file=sys.stderr)
        except json.JSONDecodeError:
            print(body, file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
