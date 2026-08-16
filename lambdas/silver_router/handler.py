"""Silver router: fired by EventBridge when a bronze _manifest.json lands,
i.e. a bronze ingestion batch completed. Reads the manifest and submits the
appropriate bronze->silver Batch jobs:

- images present        -> image-profile (metadata parquet + thumbnails)
- COCO annotation JSONs -> normalize-annotations (tidy parquet tables)

Ledgered under drop_id "silver#<manifest key>" so re-delivered events are no-ops.
"""

import json
import os
import re
from datetime import UTC, datetime

import boto3

JOB_QUEUE = os.environ["JOB_QUEUE"]
IMAGE_PROFILE_JOBDEF = os.environ["IMAGE_PROFILE_JOBDEF"]
NORMALIZE_JOBDEF = os.environ["NORMALIZE_JOBDEF"]
LEDGER_TABLE = os.environ["LEDGER_TABLE"]

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")
COCO_JSON_RE = re.compile(r"(instances|captions|person_keypoints)_.*\.json$")

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")
batch = boto3.client("batch")


def handler(event, _context):
    bucket = event["detail"]["bucket"]["name"]
    manifest_key = event["detail"]["object"]["key"]
    drop_id = f"silver#{manifest_key}"

    manifest = json.loads(s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read())
    files = [f["key"] for f in manifest["files"]]
    n_images = sum(1 for k in files if k.lower().endswith(IMAGE_SUFFIXES))
    n_coco_json = sum(1 for k in files if COCO_JSON_RE.search(k.rsplit("/", 1)[-1]))

    try:
        ddb.put_item(
            TableName=LEDGER_TABLE,
            Item={
                "drop_id": {"S": drop_id},
                "partner": {"S": manifest["partner"]},
                "src_bucket": {"S": bucket},
                "src_key": {"S": manifest_key},
                "status": {"S": "SUBMITTED"},
                "received_at": {"S": datetime.now(UTC).isoformat()},
            },
            ConditionExpression="attribute_not_exists(drop_id)",
        )
    except ddb.exceptions.ConditionalCheckFailedException:
        print(f"duplicate manifest event, skipping: {drop_id}")
        return {"drop_id": drop_id, "action": "skipped"}

    overrides = {
        "environment": [
            {"name": "BRONZE_BUCKET", "value": bucket},
            {"name": "MANIFEST_KEY", "value": manifest_key},
        ]
    }
    name_stub = re.sub(r"[^A-Za-z0-9_-]", "-", manifest_key)[:90]
    submitted = {}

    if n_images:
        job = batch.submit_job(
            jobName=f"profile-{name_stub}",
            jobQueue=JOB_QUEUE,
            jobDefinition=IMAGE_PROFILE_JOBDEF,
            containerOverrides=overrides,
        )
        submitted["image_profile"] = job["jobId"]
    if n_coco_json:
        job = batch.submit_job(
            jobName=f"normalize-{name_stub}",
            jobQueue=JOB_QUEUE,
            jobDefinition=NORMALIZE_JOBDEF,
            containerOverrides=overrides,
        )
        submitted["normalize_annotations"] = job["jobId"]

    if submitted:
        ddb.update_item(
            TableName=LEDGER_TABLE,
            Key={"drop_id": {"S": drop_id}},
            UpdateExpression="SET batch_jobs = :j",
            ExpressionAttributeValues={":j": {"S": json.dumps(submitted)}},
        )
    print(f"{drop_id}: images={n_images} coco_json={n_coco_json} jobs={submitted}")
    return {"drop_id": drop_id, "jobs": submitted}
