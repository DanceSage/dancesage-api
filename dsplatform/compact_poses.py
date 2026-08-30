"""Rewrite stored pose tracks in the compact format. Safe to re-run."""
import pathlib, sys
from .storage import ROOT, compact, _inflate

def main() -> int:
    before = after = 0
    files = sorted((ROOT / "pose").rglob("*.json"))
    for p in files:
        blob = p.read_bytes()
        before += len(blob)
        p.write_bytes(compact(_inflate(blob)))
        after += p.stat().st_size
    if not files:
        print("no pose tracks found"); return 0
    print(f"{len(files)} tracks: {before/1024/1024:.2f} MB -> {after/1024/1024:.2f} MB "
          f"({after/before*100:.0f}%, saved {(before-after)/1024/1024:.2f} MB)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
