from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import pathlib
import sys

product = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
lock = pathlib.Path(sys.argv[3])
items = []
for dist in sorted(metadata.distributions(path=[str(target)]), key=lambda item: (item.metadata.get("Name") or "").lower()):
    items.append({"name": dist.metadata.get("Name"), "version": dist.version})
manifest = {
    "schema_version": "1.0",
    "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
    "interpreter": sys.version,
    "target": str(target),
    "distributions": items,
}
(product / "runtime" / "install-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, sort_keys=True))
