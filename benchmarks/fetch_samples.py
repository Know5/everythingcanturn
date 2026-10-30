"""Download the public PDFs used by the PDF→Word capability benchmark.

Files stay under `benchmarks/samples/` inside the repository. Nothing is written
outside the project tree.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"

FILES = {
    "attention.pdf": (
        "https://arxiv.org/pdf/1706.03762",
        "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
    ),
    "fw9.pdf": (
        "https://www.irs.gov/pub/irs-pdf/fw9.pdf",
        "2d420cbb4123dcf1fb82595b2359cfbb5d81f00b9df9d359fcc7af361d093f53",
    ),
    "scanned.pdf": (
        "https://pdftoolskit.org/samples/sample-scanned.pdf",
        "d32a62e246778d856c85b03bfc6c91c36c9a071756b733f78daf46b4253de634",
    ),
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, (url, expected) in FILES.items():
        path = SAMPLES / name
        if path.is_file() and digest(path) == expected:
            print(f"ok      {name}")
            continue
        print(f"fetch   {name} <- {url}")
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "convcore-benchmark/1.0"}
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                path.write_bytes(response.read())
        except Exception as exc:
            print(f"failed  {name}: {exc}", file=sys.stderr)
            failed = True
            continue
        actual = digest(path)
        if actual != expected:
            print(
                f"hash mismatch {name}: {actual} != {expected}",
                file=sys.stderr,
            )
            failed = True
        else:
            print(f"ok      {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
