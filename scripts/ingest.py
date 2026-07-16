"""
scripts/ingest.py
──────────────────
CLI script for ingesting PDF documents into the database.

Usage:
    python scripts/ingest.py --pdf data/ct200_manual.pdf --version 1
    python scripts/ingest.py --pdf data/ct200_manual_v2.pdf --version 2

This is a standalone script (not a FastAPI route) intended for initial data
loading and automation.  It uses the same ingestion service as the API.

Options:
    --pdf     PATH     Path to the PDF file (required).
    --version INT      Version number to assign (required, must be >= 1).
    --title   TEXT     Optional document title override.
    --db-url  TEXT     Database URL (defaults to value in .env / settings).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the project root is on the path when running as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging_config import setup_logging
from app.database.base import Base, engine
from app.database.session import SessionLocal
from app.services.ingestion_service import ingest_pdf
from app.services.staleness_service import run_staleness_check

setup_logging()

import logging
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest a PDF document into the qa-backend database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--pdf",
        required=True,
        type=Path,
        help="Path to the PDF file to ingest.",
    )
    parser.add_argument(
        "--version",
        required=True,
        type=int,
        metavar="N",
        help="Version number to assign (e.g. 1 for first ingest, 2 for second).",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Optional document title override.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        logger.error("PDF file not found: %s", pdf_path)
        sys.exit(1)

    if args.version < 1:
        logger.error("Version number must be >= 1.")
        sys.exit(1)

    # Ensure tables exist.
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        try:
            logger.info("Starting ingestion: %s (version=%d)", pdf_path.name, args.version)
            result = ingest_pdf(
                db=db,
                pdf_path=pdf_path,
                version_number=args.version,
                document_title=args.title,
            )
            db.commit()

            print("\n" + "=" * 60)
            print("  INGESTION SUCCESSFUL")
            print("=" * 60)
            print(f"  Document ID   : {result.document_id}")
            print(f"  Version ID    : {result.version_id}")
            print(f"  Version Number: {result.version_number}")
            print(f"  Nodes Created : {result.nodes_created}")
            print(f"  File Hash     : {result.file_hash[:16]}...")
            print(f"  Message       : {result.message}")
            print("=" * 60 + "\n")

            # Trigger staleness check when ingesting version 2+.
            if args.version > 1:
                logger.info("Running staleness check for document %d ...", result.document_id)
                newly_stale = run_staleness_check(db, result.document_id)
                db.commit()
                if newly_stale:
                    print(f"  [WARN] {newly_stale} generation(s) marked as STALE.")
                else:
                    print("  [OK] No existing generations were affected by this version.")
                print()

        except Exception as exc:
            db.rollback()
            logger.error("Ingestion failed: %s", exc)
            sys.exit(1)


if __name__ == "__main__":
    main()
