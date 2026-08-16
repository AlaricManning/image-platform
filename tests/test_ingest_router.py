import os
import sys
from pathlib import Path

os.environ.setdefault("BRONZE_BUCKET", "test-bronze")
os.environ.setdefault("LEDGER_TABLE", "test-ledger")
os.environ.setdefault("JOB_QUEUE", "test-queue")
os.environ.setdefault("JOB_DEFINITION", "test-jobdef")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambdas" / "ingest_router"))

from handler import partner_from_bucket  # noqa: E402


def test_partner_from_bucket():
    assert partner_from_bucket("imgp-dev-landing-coco-935961368629") == "coco"


def test_partner_with_hyphenated_name():
    assert partner_from_bucket("imgp-dev-landing-acme-media-935961368629") == "acme-media"


def test_partner_unknown_for_non_landing_bucket():
    assert partner_from_bucket("imgp-dev-bronze-935961368629") == "unknown"
    assert partner_from_bucket("some-other-bucket") == "unknown"
