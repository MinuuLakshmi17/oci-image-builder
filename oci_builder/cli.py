from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .builder import BuildError, build_image, load_spec


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="oci-image-builder", description="Build an OCI image layout without a container daemon")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate a YAML spec")
    validate.add_argument("spec")
    build = sub.add_parser("build", help="build an OCI image layout directory")
    build.add_argument("spec")
    build.add_argument("--output", "-o", required=True, help="output OCI layout directory (must be empty or absent)")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            spec, _ = load_spec(args.spec)
            print(f"valid: {spec['name']}:{spec.get('tag', 'latest')}")
        else:
            out = build_image(args.spec, args.output)
            print(json.dumps({"layout": str(out), "index": str(out / "index.json")}, indent=2))
        return 0
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
