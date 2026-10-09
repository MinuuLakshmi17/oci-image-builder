# oci-image-builder

A small educational builder that creates an **OCI image layout** from a YAML spec using only Python plus PyYAML. It does not require Docker or a container daemon. It manually creates deterministic uncompressed tar layers, an OCI image config, a manifest, `index.json`, and `oci-layout`.

> Scope: this is a learning project, not a production replacement for Rockcraft, BuildKit, or `umoci`. It emits an OCI image layout directory, not a Docker archive or a registry upload. Runtime execution still depends on the image containing the needed executable and libraries.

## Install and test

```bash
python -m pip install -e ".[test]"
pytest -q
```

## Validate and build

From the repository root:

```bash
oci-image-builder validate examples/image.yaml
oci-image-builder build examples/image.yaml --output dist/hello-oci
```

The output directory is an OCI image layout and must be absent or empty. It contains `oci-layout`, `index.json`, and `blobs/sha256/*`. Set `SOURCE_DATE_EPOCH` to choose the layer timestamp; default is epoch zero for reproducibility.

## YAML spec

```yaml
name: hello-oci
tag: "1.0"
architecture: amd64
os: linux
layers:
  - source: app       # directory relative to this YAML file
    destination: /app # absolute path inside image
config:
  WorkingDir: /app
  Entrypoint: ["/bin/sh", "-c"]
  Cmd: ["echo hello"]
  Env: ["APP_ENV=demo"]
  Labels:
    org.opencontainers.image.description: "Example"
```

Each layer is a directory copied into an uncompressed tar layer. Layer sources must remain under the spec directory; absolute symlinks and symlinks escaping the source tree are rejected. The builder does not execute build steps or install packages.

## Current limitations

- One simple directory-copy operation per layer; no `RUN`, package manager, build stages, whiteouts/deletions, file ownership configuration, or compression.
- Only basic OCI image config fields are exposed.
- Architecture/OS values are recorded but not validated against a registry schema.
- The OCI layout structure and digest references are unit-tested; this project has not been validated with every OCI runtime or registry. For deeper validation, use tools such as `umoci` or `skopeo` when installed.
- Deterministic metadata is applied to tar entries, but unusual filesystem objects and all platform-specific edge cases are outside scope.
