import os

from conftest import load_lambda_handler

os.environ.setdefault("BRONZE_BUCKET", "test-bronze")
os.environ.setdefault("LEDGER_TABLE", "test-ledger")
os.environ.setdefault("JOB_QUEUE", "test-queue")
os.environ.setdefault("JOB_DEFINITION", "test-jobdef")

handler = load_lambda_handler("ingest_router")


def test_partner_from_bucket():
    assert handler.partner_from_bucket("imgp-dev-landing-coco-935961368629") == "coco"


def test_partner_with_hyphenated_name():
    assert handler.partner_from_bucket("imgp-dev-landing-acme-media-935961368629") == "acme-media"


def test_partner_unknown_for_non_landing_bucket():
    assert handler.partner_from_bucket("imgp-dev-bronze-935961368629") == "unknown"
    assert handler.partner_from_bucket("some-other-bucket") == "unknown"
