"""Deterministic input identities and verifiable artifact relationships."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@lru_cache(maxsize=1)
def source_identity() -> dict:
    """The content digest identifies executing source without a Git checkout."""
    package = Path(__file__).resolve().parent
    root = package.parent.parent
    files = {f"src/harpy/{p.name}": file_digest(p) for p in sorted(package.glob("*.py"))}
    for name in ("pyproject.toml", "uv.lock"):
        if (root / name).is_file():
            files[name] = file_digest(root / name)
    return {"files_sha256": files, "tree_sha256": digest(files)}


def capture_inputs(config: dict, pricing: dict) -> dict:
    config = deepcopy(config)
    priced = {k: deepcopy(v) for k, v in pricing.items() if k != "path"}
    identity = deepcopy(source_identity())
    hashes = {
        "config_sha256": digest(config),
        "pricing_sha256": digest(priced),
        "source_tree_sha256": identity["tree_sha256"],
    }
    return {
        "schema": "harpy/inputs/1",
        "config": config,
        "pricing": priced,
        "source": identity,
        **hashes,
        "experiment_fingerprint": digest(hashes),
    }


def verify_inputs(inputs: dict) -> list[str]:
    errors = []
    checks = {
        "config_sha256": digest(inputs["config"]),
        "pricing_sha256": digest(inputs["pricing"]),
        "source_tree_sha256": digest(inputs["source"]["files_sha256"]),
    }
    for field, expected in checks.items():
        if inputs.get(field) != expected:
            errors.append(f"inconsistent input hash: {field}")
    if inputs["source"].get("tree_sha256") != checks["source_tree_sha256"]:
        errors.append("inconsistent source tree hash")
    if inputs.get("experiment_fingerprint") != digest(checks):
        errors.append("inconsistent experiment fingerprint")
    return errors


def write_manifest(directory: str | Path) -> Path:
    """Link artifacts to recorded inputs. A hash is not a digital signature."""
    directory = Path(directory)
    artifacts = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path == directory / "manifest.json":
            continue
        if path.is_symlink():
            raise ValueError("artifact manifests do not follow symbolic links")
        entry = {
            "path": path.relative_to(directory).as_posix(),
            "sha256": file_digest(path),
            "size_bytes": path.stat().st_size,
        }
        if path.suffix == ".json":
            document = json.loads(path.read_text(encoding="utf-8"))
            inputs = document.get("provenance", {})
            if inputs.get("experiment_fingerprint"):
                if verify_inputs(inputs):
                    raise ValueError(f"artifact has inconsistent provenance: {path}")
                entry["parents"] = {
                    k: inputs[k] for k in ("config_sha256", "pricing_sha256", "source_tree_sha256")
                }
        artifacts.append(entry)
    path = directory / "manifest.json"
    path.write_text(
        json.dumps(
            {"schema": "harpy/artifact-manifest/1", "artifacts": artifacts},
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def verify_manifest(path: str | Path) -> list[str]:
    path = Path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "harpy/artifact-manifest/1":
        raise ValueError("unsupported artifact manifest")
    root = path.parent.resolve()
    errors, seen = [], set()
    for entry in document["artifacts"]:
        relative = entry["path"]
        target = root / relative
        if relative in seen:
            errors.append(f"duplicate artifact: {relative}")
        seen.add(relative)
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            errors.append(f"unsafe artifact path: {relative}")
        elif not target.is_file():
            errors.append(f"missing artifact: {relative}")
        elif target.stat().st_size != entry["size_bytes"] or file_digest(target) != entry["sha256"]:
            errors.append(f"changed artifact: {relative}")
        elif target.suffix == ".json":
            artifact = json.loads(target.read_text(encoding="utf-8"))
            inputs = artifact.get("provenance", {})
            if inputs.get("experiment_fingerprint"):
                errors.extend(f"{relative}: {e}" for e in verify_inputs(inputs))
                expected = {
                    k: inputs[k] for k in ("config_sha256", "pricing_sha256", "source_tree_sha256")
                }
                if entry.get("parents") != expected:
                    errors.append(f"inconsistent artifact parents: {relative}")
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.resolve() != path.resolve()
    }
    errors.extend(f"unlisted artifact: {p}" for p in sorted(actual - seen))
    return errors
