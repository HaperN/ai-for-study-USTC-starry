from __future__ import annotations

import argparse
from pathlib import Path

from .direct_document_audit import audit_document_files
from .diagram_observer import build_subprocess_diagram_observer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit one finished analysis directly against one frozen source."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--diagram-observer")
    parser.add_argument("--diagram-observer-work-root")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if bool(args.diagram_observer) != bool(args.diagram_observer_work_root):
        parser.error(
            "--diagram-observer and --diagram-observer-work-root must be supplied together"
        )
    observer = (
        build_subprocess_diagram_observer(
            args.diagram_observer,
            work_root=Path(args.diagram_observer_work_root),
        )
        if args.diagram_observer
        else None
    )
    audit_document_files(
        args.source,
        args.analysis,
        args.output,
        diagram_observer=observer,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
