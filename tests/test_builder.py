import hashlib
import json
from pathlib import Path
import tarfile

import pytest
import yaml

from oci_builder.builder import BuildError, build_image, load_spec

EXAMPLE = Path(__file__).parents[1] / "examples" / "image.yaml"


def blob(root, descriptor):
    return (root / "blobs" / "sha256" / descriptor["digest"].split(":")[1]).read_bytes()


def test_build_writes_valid_layout_and_digest_links(tmp_path):
    out = build_image(EXAMPLE, tmp_path / "image")
    assert json.loads((out / "oci-layout").read_text())["imageLayoutVersion"] == "1.0.0"
    index = json.loads((out / "index.json").read_text())
    manifest_desc = index["manifests"][0]
    manifest_bytes = blob(out, manifest_desc)
    assert "sha256:" + hashlib.sha256(manifest_bytes).hexdigest() == manifest_desc["digest"]
    manifest = json.loads(manifest_bytes)
    config = json.loads(blob(out, manifest["config"]))
    assert config["config"]["WorkingDir"] == "/app"
    assert len(manifest["layers"]) == 1
    layer_bytes = blob(out, manifest["layers"][0])
    with tarfile.open(fileobj=__import__("io").BytesIO(layer_bytes)) as tar:
        assert tar.extractfile("app/hello.txt").read().startswith(b"Hello")


def test_rejects_nonempty_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep").write_text("x")
    with pytest.raises(BuildError, match="must be empty"):
        build_image(EXAMPLE, out)


def test_rejects_missing_source(tmp_path):
    spec = {"name": "bad", "layers": [{"source": "nope"}]}
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(spec))
    with pytest.raises(BuildError, match="does not exist"):
        load_spec(p)


def test_rejects_source_escape(tmp_path):
    spec = {"name": "bad", "layers": [{"source": "../outside"}]}
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(spec))
    with pytest.raises(BuildError, match="stay inside"):
        load_spec(p)


def test_build_is_reproducible(tmp_path):
    a = build_image(EXAMPLE, tmp_path / "a")
    b = build_image(EXAMPLE, tmp_path / "b")
    assert (a / "index.json").read_bytes() == (b / "index.json").read_bytes()
    assert sorted(p.name for p in (a / "blobs" / "sha256").iterdir()) == sorted(p.name for p in (b / "blobs" / "sha256").iterdir())
