"""Download the EMSx dataset from Zenodo record 5510400 with hash verification.

Honest acquisition only: this fetches files, verifies size and MD5 against the
Zenodo record, and records a manifest. It does NOT evaluate any model on the data
and does NOT establish forecasting skill. By default it fetches the small,
high-value files (metadata.csv, pv.csv.gz, and a few site files); pass --all to
fetch the full ~6.2 GB record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

API = "https://zenodo.org/api/records/5510400"
OUT = Path(__file__).resolve().parent / "data_samples" / "emsx"
# Small, high-value subset: battery metadata, normalized PV profile, two site files.
DEFAULT_SUBSET = {"metadata.csv", "pv.csv.gz", "1.csv.gz", "2.csv.gz"}


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="download the full ~6.2 GB record")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(API, headers={"User-Agent": "EMSx-dataset-downloader/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        record = json.load(response)
    files = record["files"]
    wanted = files if args.all else [f for f in files if f["key"] in DEFAULT_SUBSET]
    print(f"Record lists {len(files)} files; fetching {len(wanted)}")
    manifest = []
    for item in wanted:
        name = item["key"]
        url = item["links"]["self"]
        path = OUT / name
        expected_md5 = (item.get("checksum") or "").split(":")[-1]
        if path.exists() and path.stat().st_size == item["size"]:
            print(f"Already complete: {name}")
        else:
            print(f"Downloading {name} ({item['size'] / 1e6:.1f} MB)")
            file_request = urllib.request.Request(url, headers={"User-Agent": "EMSx-dataset-downloader/1.0"})
            with urllib.request.urlopen(file_request, timeout=300) as response, path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        actual_md5 = _md5(path)
        ok = path.stat().st_size == item["size"] and (not expected_md5 or actual_md5 == expected_md5)
        manifest.append({"name": name, "size": path.stat().st_size, "expected_size": item["size"], "md5": actual_md5, "expected_md5": expected_md5, "verified": ok})
        print(f"  size={path.stat().st_size} md5={actual_md5} verified={ok}")
    (OUT / "acquisition_manifest.json").write_text(json.dumps({"record": API, "files": manifest}, indent=2), encoding="utf-8")
    verified = sum(1 for entry in manifest if entry["verified"])
    print(f"Done. Verified {verified}/{len(manifest)} files. Manifest at {OUT / 'acquisition_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
