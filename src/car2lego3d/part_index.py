"""Search native parts without inventing colour availability or connector metadata."""
import json
import os
from pathlib import Path
import subprocess

from .common import ROOT, digest, write_json
from .gpt_reference import configuration


INDEX_VERSION = "native-part-index-v1"


def build_index(directory, parts):
    source, python = configuration()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory/"part-index.json"
    selected = sorted({p.removesuffix(".dat") for p in parts})
    subprocess.run([python, "-B", str(ROOT/"scripts/native_audit_worker.py"), "--source", str(source),
        "--parts", *selected, "--index", "--output", str(output)], check=True, timeout=300,
        capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    result = json.loads(output.read_text(encoding="utf-8"))
    result.update(schema_version=1, version=INDEX_VERSION, requested_parts=selected)
    write_json(output, result)
    return result


def query(index, text="", transparent=None, max_size_mm=None, require_native=True):
    result = []
    for name, part in index["catalog"].items():
        validity = part.get("validity", {})
        if require_native and not all(validity.get(k) for k in ("geometry", "connections", "collider")):
            continue
        if text.lower() not in (name+" "+part.get("description", "")).lower():
            continue
        if transparent is not None and part.get("transparent_capable") != transparent:
            continue
        if max_size_mm and any(a > b for a, b in zip(part["size_mm"], max_size_mm)):
            continue
        result.append(dict(part=name, **part))
    return sorted(result, key=lambda p: p["part"])
