"""GEFCom2014 recursive-archive loader (design §8.4.2).

The archive is nested zips. The solar ``benchmark`` members are zero-padded for
tasks 1-9 and unpadded for 10-15, so the member resolver tries both spellings and
records which matched. STORED members are opened as nested ``zipfile.ZipFile``
over a seekable slice. A member absent from the expected list is a
``DatasetLayoutError`` naming members actually found. SSRD de-accumulation is
mandatory for the solar track (handled in :mod:`schema`).

Data as journal supplementary archive (Hong, Pinson, Fan, Zareipour, Troccoli &
Hyndman 2016). The exact variable lists are pinned per §8.4.2.
"""

from __future__ import annotations

import io
import zipfile

from ..errors import DatasetLayoutError

DEPTH_CAP = 3
_MEMBER_SIZE_CAP = 64 * 1024 * 1024

# Verbatim solar variables (§8.4.2); no DNI/DHI, no coordinates.
SOLAR_VARIABLES = [
    "078.128", "079.128", "134.128", "157.128", "164.128", "165.128",
    "166.128", "167.128", "169.128", "175.128", "178.128", "228.128",
]
# Wind: u,v at 10 m and 100 m only (§8.4.2).
WIND_VARIABLES = ["U10", "V10", "U100", "V100"]


def resolve_benchmark_member(names: list[str], task: int) -> str:
    """Resolve the solar benchmark member for a task, trying both spellings.

    Zero-padded ``benchmark0{N}.csv`` for N in 1..9, unpadded ``benchmark{N}.csv``
    for N in 10..15. Returns the matched name or raises DatasetLayoutError.
    """
    candidates = [f"benchmark{task:02d}.csv", f"benchmark{task}.csv"]
    present = set(names)
    for c in candidates:
        for n in names:
            if n.endswith(c):
                return n
    raise DatasetLayoutError(
        f"solar benchmark for task {task} not found (tried {candidates}); members: {sorted(present)[:10]}"
    )


def open_nested_zip(outer: zipfile.ZipFile, member: str) -> zipfile.ZipFile:
    """Open a nested zip member as a ZipFile (STORED members are seekable)."""
    info = _find_info(outer, member)
    if info.file_size > _MEMBER_SIZE_CAP:
        raise DatasetLayoutError(f"member {member} exceeds size cap {_MEMBER_SIZE_CAP}")
    data = outer.read(info.filename)
    return zipfile.ZipFile(io.BytesIO(data))


def _find_info(zf: zipfile.ZipFile, member: str) -> zipfile.ZipInfo:
    for info in zf.infolist():
        if info.filename == member or info.filename.endswith("/" + member) or info.filename.endswith(member):
            return info
    raise DatasetLayoutError(f"member {member!r} absent; found {[i.filename for i in zf.infolist()][:10]}")


def list_members(zf: zipfile.ZipFile) -> list[str]:
    return [i.filename for i in zf.infolist()]


def enforce_depth(depth: int) -> None:
    if depth > DEPTH_CAP:
        raise DatasetLayoutError(f"archive depth {depth} exceeds cap {DEPTH_CAP}")


__all__ = [
    "DEPTH_CAP",
    "SOLAR_VARIABLES",
    "WIND_VARIABLES",
    "resolve_benchmark_member",
    "open_nested_zip",
    "list_members",
    "enforce_depth",
]
