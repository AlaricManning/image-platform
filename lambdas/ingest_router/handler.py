"""Ingestion router: fired by EventBridge for every object created in a landing
bucket. Records the drop in the DynamoDB ledger, then routes by type — archives
go to the unpack-archive Batch job, loose objects are copied straight to bronze.

Idempotency: the ledger put is conditional on drop_id (partner#key#etag), so a
replayed event for the same object version is a no-op.
"""

import os
import re
from datetime import UTC, datetime

import boto3

BRONZE_BUCKET = os.environ["BRONZE_BUCKET"]
LEDGER_TABLE = os.environ["LEDGER_TABLE"]
JOB_QUEUE = os.environ["JOB_QUEUE"]
JOB_DEFINITION = os.environ["JOB_DEFINITION"]

ARCHIVE_SUFFIXES = (".zip",)

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")
batch = boto3.client("batch")


def partner_from_bucket(bucket: str) -> str:
    # imgp-dev-landing-<partner>-<account_id>
    m = re.match(r"^imgp-[^-]+-landing-(.+)-\d{12}$", bucket)
    return m.group(1) if m else "unknown"


def handler(event, _context):
    detail = event["detail"]
    bucket = detail["bucket"]["name"]
    key = detail["object"]["key"]
    size = detail["object"].get("size", 0)
    etag = detail["object"].get("etag", "")

    partner = partner_from_bucket(bucket)
    ingest_date = event["time"][:10]
    drop_id = f"{partner}#{key}#{etag}"
    now = datetime.now(UTC).isoformat()

    try:
        ddb.put_item(
            TableName=LEDGER_TABLE,
            Item={
                "drop_id": {"S": drop_id},
                "partner": {"S": partner},
                "src_bucket": {"S": bucket},
                "src_key": {"S": key},
                "size_bytes": {"N": str(size)},
                "ingest_date": {"S": ingest_date},
                "status": {"S": "RECEIVED"},
                "received_at": {"S": now},
            },
            ConditionExpression="attribute_not_exists(drop_id)",
        )
    except ddb.exceptions.ConditionalCheckFailedException:
        print(f"duplicate event, already ledgered: {drop_id}")
        return {"drop_id": drop_id, "action": "skipped"}

    if key.lower().endswith(ARCHIVE_SUFFIXES):
        job_name = "unpack-" + re.sub(r"[^A-Za-z0-9_-]", "-", key)[:100]
        job = batch.submit_job(
            jobName=job_name,
            jobQueue=JOB_QUEUE,
            jobDefinition=JOB_DEFINITION,
            containerOverrides={
                "environment": [
                    {"name": "SRC_BUCKET", "value": bucket},
                    {"name": "SRC_KEY", "value": key},
                    {"name": "PARTNER", "value": partner},
                    {"name": "INGEST_DATE", "value": ingest_date},
                    {"name": "DROP_ID", "value": drop_id},
                ]
            },
        )
        _set_status(drop_id, "SUBMITTED", job_id=job["jobId"])
        return {"drop_id": drop_id, "action": "batch", "job_id": job["jobId"]}

    # Loose (non-archive) object: copy straight to bronze under the standard scheme.
    dest = f"partner={partner}/dataset=loose/ingest_date={ingest_date}/{key.rsplit('/', 1)[-1]}"
    s3.copy_object(
        Bucket=BRONZE_BUCKET,
        Key=dest,
        CopySource={"Bucket": bucket, "Key": key},
    )
    _set_status(drop_id, "DONE", bronze_key=dest)
    return {"drop_id": drop_id, "action": "copied", "bronze_key": dest}


def _set_status(drop_id: str, status: str, job_id: str = "", bronze_key: str = ""):
    updates = {"#s": "status"}
    expr = "SET #s = :s, updated_at = :t"
    values = {
        ":s": {"S": status},
        ":t": {"S": datetime.now(UTC).isoformat()},
    }
    if job_id:
        expr += ", batch_job_id = :j"
        values[":j"] = {"S": job_id}
    if bronze_key:
        expr += ", bronze_key = :b"
        values[":b"] = {"S": bronze_key}
    ddb.update_item(
        TableName=LEDGER_TABLE,
        Key={"drop_id": {"S": drop_id}},
        UpdateExpression=expr,
        ExpressionAttributeNames=updates,
        ExpressionAttributeValues=values,
    )
