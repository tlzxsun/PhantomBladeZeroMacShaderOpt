#!/usr/bin/env python3
"""Read-only inspection of the privately observed Metal functions.list format."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys


def default_cache_root():
    try:
        result = subprocess.run(["getconf", "DARWIN_USER_CACHE_DIR"], capture_output=True, text=True, check=True)
        if result.stdout.strip():
            return Path(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    # getconf can be denied inside a sandbox; Darwin's T and C folders are siblings.
    temporary = os.environ.get("TMPDIR")
    if temporary:
        candidate = Path(temporary).parent / "C"
        if candidate.is_dir():
            return candidate
    raise ValueError("Cannot discover the user cache directory; pass --cache-root explicitly.")


def inspect(path):
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) < 44 or raw[:4] != bytes.fromhex("45362718"):
        raise ValueError("Unrecognized private cache format: " + str(path))
    records = {}
    for offset in range(44, len(raw) - 55, 56):
        size, position, key, flag = struct.unpack_from("<QQ32sQ", raw, offset)
        if flag == 0 and size:
            records[key] = (size, position)
        elif flag == 1:
            records.pop(key, None)
        else:
            raise ValueError("Unrecognized record: " + str(path))
    active = sum(record[0] for record in records.values())
    data = path.with_suffix(".data")
    return {"path": str(path), "entries": len(records), "active_bytes": active,
            "active_GiB": round(active / 1073741824, 4),
            "data_file_bytes": data.stat().st_size if data.exists() else None,
            "incomplete_tail_bytes": (len(raw) - 44) % 56,
            "index_mtime": path.stat().st_mtime}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, help="Override DARWIN_USER_CACHE_DIR")
    args = parser.parse_args()
    root = args.cache_root
    if root is None:
        if sys.platform != "darwin":
            parser.error("--cache-root is required outside macOS")
        try:
            root = default_cache_root()
        except ValueError as error:
            parser.error(str(error))
    rows = []
    for backend in ("d3dm", "mullion"):
        base = root / backend / "PBZ.exe"
        if base.exists():
            try:
                rows.extend(inspect(path) for path in sorted(base.rglob("functions.list")))
            except (OSError, ValueError) as error:
                parser.error(str(error))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("Live index writes may be incomplete. Format is private; this tool does not change cache files.", file=sys.stderr)


if __name__ == "__main__":
    main()
