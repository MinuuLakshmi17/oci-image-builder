from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

OCI_VERSION = "1.0.0"
OCI_IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_IMAGE_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER_TAR = "application/vnd.oci.image.layer.v1.tar"
OCI_LAYOUT_VERSION = "1.0.0"


class BuildError(ValueError):
    """Invalid spec or build input."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _descriptor(media_type: str, data: bytes) -> dict[str, Any]:
    return {"mediaType": media_type, "digest": _digest(data), "size": len(data)}


def load_spec(spec_path: str | Path) -> tuple[dict[str, Any], Path]:
    path = Path(spec_path).resolve()
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BuildError(f"cannot read YAML spec {path}: {exc}") from exc
    if not isinstance(spec, dict):
        raise BuildError("spec root must be a YAML mapping")
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        raise BuildError("spec requires a non-empty 'name'")
    if not isinstance(spec.get("tag", "latest"), str):
        raise BuildError("'tag' must be a string")
    layers = spec.get("layers")
    if not isinstance(layers, list) or not layers:
        raise BuildError("spec requires a non-empty 'layers' list")
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict) or not isinstance(layer.get("source"), str):
            raise BuildError(f"layers[{i}] requires a string 'source'")
        destination = layer.get("destination", "/")
        if not isinstance(destination, str) or not destination.startswith("/"):
            raise BuildError(f"layers[{i}].destination must be an absolute container path")
        source = (path.parent / layer["source"]).resolve()
        try:
            source.relative_to(path.parent.resolve())
        except ValueError as exc:
            raise BuildError(f"layers[{i}].source must stay inside the spec directory") from exc
        if not source.exists():
            raise BuildError(f"layers[{i}] source does not exist: {layer['source']}")
        if not source.is_dir():
            raise BuildError(f"layers[{i}] source must be a directory")
    config = spec.get("config", {})
    if not isinstance(config, dict):
        raise BuildError("'config' must be a mapping")
    env = config.get("Env", [])
    if not isinstance(env, list) or not all(isinstance(x, str) and "=" in x for x in env):
        raise BuildError("config.Env must be a list of KEY=VALUE strings")
    entrypoint = config.get("Entrypoint", [])
    cmd = config.get("Cmd", [])
    for key, value in (("Entrypoint", entrypoint), ("Cmd", cmd)):
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise BuildError(f"config.{key} must be a list of strings")
    return spec, path.parent


def _safe_destination(destination: str, relative: Path) -> str:
    base = PurePosixPath(destination)
    combined = base.joinpath(*relative.parts)
    normalized = PurePosixPath(os.path.normpath(str(combined)))
    if not normalized.is_absolute() or ".." in normalized.parts:
        raise BuildError(f"layer path escapes container root: {relative}")
    return str(normalized).lstrip("/")


def _make_layer(source: Path, destination: str, output: Path, epoch: int) -> tuple[bytes, dict[str, Any]]:
    """Create a deterministic uncompressed OCI layer tar; reject escaping symlinks."""
    with tempfile.NamedTemporaryFile() as tmp:
        with tarfile.open(fileobj=tmp, mode="w", format=tarfile.PAX_FORMAT) as tar:
            root_name = destination.strip("/")
            if root_name:
                info = tarfile.TarInfo(root_name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                tar.addfile(info)
            for item in sorted(source.rglob("*"), key=lambda p: p.relative_to(source).as_posix()):
                rel = item.relative_to(source)
                arcname = _safe_destination(destination, rel)
                if item.is_symlink():
                    target = os.readlink(item)
                    if os.path.isabs(target):
                        raise BuildError(f"absolute symlink is not allowed: {item}")
                    resolved = (item.parent / target).resolve()
                    try:
                        resolved.relative_to(source.resolve())
                    except ValueError as exc:
                        raise BuildError(f"symlink escapes source tree: {item}") from exc
                info = tar.gettarinfo(str(item), arcname=arcname)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                info.pax_headers = {}
                if info.isfile():
                    with item.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)
        tmp.seek(0)
        data = tmp.read()
    return data, {"mediaType": OCI_LAYER_TAR, "digest": _digest(data), "size": len(data)}


def build_image(spec_path: str | Path, output_dir: str | Path) -> Path:
    spec, base = load_spec(spec_path)
    out = Path(output_dir).resolve()
    if out.exists() and any(out.iterdir()):
        raise BuildError(f"output directory must be empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    blobs = out / "blobs" / "sha256"
    blobs.mkdir(parents=True, exist_ok=True)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    layer_descriptors = []
    diff_ids = []
    for layer in spec["layers"]:
        source = (base / layer["source"]).resolve()
        data, descriptor = _make_layer(source, layer.get("destination", "/"), out, epoch)
        (blobs / descriptor["digest"].split(":", 1)[1]).write_bytes(data)
        layer_descriptors.append(descriptor)
        diff_ids.append(_digest(data))

    config_spec = spec.get("config", {})
    config = {
        "created": spec.get("created", "1970-01-01T00:00:00Z"),
        "architecture": spec.get("architecture", "amd64"),
        "os": spec.get("os", "linux"),
        "config": {
            "Env": config_spec.get("Env", []),
            "Entrypoint": config_spec.get("Entrypoint", []),
            "Cmd": config_spec.get("Cmd", []),
            "WorkingDir": config_spec.get("WorkingDir", "/"),
            "User": config_spec.get("User", ""),
            "Labels": config_spec.get("Labels", {}),
        },
        "rootfs": {"type": "layers", "diff_ids": diff_ids},
        "history": [{"created": spec.get("created", "1970-01-01T00:00:00Z"), "created_by": f"oci-image-builder layer {i + 1}"} for i in range(len(layer_descriptors))],
    }
    config_bytes = _json_bytes(config)
    config_desc = _descriptor(OCI_IMAGE_CONFIG, config_bytes)
    (blobs / config_desc["digest"].split(":", 1)[1]).write_bytes(config_bytes)

    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_IMAGE_MANIFEST,
        "config": config_desc,
        "layers": layer_descriptors,
        "annotations": {"org.opencontainers.image.title": spec["name"], "org.opencontainers.image.version": spec.get("tag", "latest")},
    }
    manifest_bytes = _json_bytes(manifest)
    manifest_desc = _descriptor(OCI_IMAGE_MANIFEST, manifest_bytes)
    (blobs / manifest_desc["digest"].split(":", 1)[1]).write_bytes(manifest_bytes)
    index = {"schemaVersion": 2, "manifests": [{**manifest_desc, "annotations": {"org.opencontainers.image.ref.name": f"{spec['name']}:{spec.get('tag', 'latest')}"}}]}
    (out / "index.json").write_bytes(_json_bytes(index))
    (out / "oci-layout").write_bytes(_json_bytes({"imageLayoutVersion": OCI_LAYOUT_VERSION}))
    return out
