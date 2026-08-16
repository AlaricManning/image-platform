import os

from conftest import load_lambda_handler

os.environ.setdefault("JOB_QUEUE", "test-queue")
os.environ.setdefault("IMAGE_PROFILE_JOBDEF", "test-profile")
os.environ.setdefault("NORMALIZE_JOBDEF", "test-normalize")
os.environ.setdefault("LEDGER_TABLE", "test-ledger")

handler = load_lambda_handler("silver_router")


def test_image_suffix_matching():
    assert "p/d/000000397133.jpg".lower().endswith(handler.IMAGE_SUFFIXES)
    assert not "p/d/_manifest.json".lower().endswith(handler.IMAGE_SUFFIXES)


def test_coco_json_matching():
    assert handler.COCO_JSON_RE.search("instances_val2017.json")
    assert handler.COCO_JSON_RE.search("captions_train2017.json")
    assert handler.COCO_JSON_RE.search("person_keypoints_val2017.json")
    assert not handler.COCO_JSON_RE.search("_manifest.json")
    assert not handler.COCO_JSON_RE.search("000000397133.jpg")
