"""unpack-archive Batch job: download an archive from a landing bucket, extract
it into bronze under the standard key scheme, write a manifest, and update the
ingestion ledger.

Bronze layout for a drop of <name>.zip:
  partner=<p>/dataset=<name>/ingest_date=<d>/<entry path>
  partner=<p>/dataset=<name>/ingest_date=<d>/_manifest.json

Idempotent: same archive always produces the same keys, so re-runs overwrite
identical data.
"""

import json
import mimetypes
import os
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import boto3

UPLOAD_WORKERS = 16


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required env var {name}")
    return value


def set_status(ddb, table: str, drop_id: str, status: str, **attrs: str):
    expr = "SET #s = :s, updated_at = :t"
    values = {
        ":s": {"S": status},
        ":t": {"S": datetime.now(UTC).isoformat()},
    }
    for i, (k, v) in enumerate(attrs.items()):
        expr += f", {k} = :a{i}"
        values[f":a{i}"] = {"S": v}
    ddb.update_item(
        TableName=table,
        Key={"drop_id": {"S": drop_id}},
        UpdateExpression=expr,
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=values,
    )


def main() -> int:
    src_bucket = env("SRC_BUCKET")
    src_key = env("SRC_KEY")
    partner = env("PARTNER")
    ingest_date = env("INGEST_DATE")
    drop_id = env("DROP_ID")
    bronze_bucket = env("BRONZE_BUCKET")
    ledger_table = env("LEDGER_TABLE")

    s3 = boto3.client("s3")
    ddb = boto3.client("dynamodb")

    dataset = Path(src_key).stem
    prefix = f"partner={partner}/dataset={dataset}/ingest_date={ingest_date}/"
    set_status(ddb, ledger_table, drop_id, "UNPACKING", bronze_prefix=prefix)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, "archive.zip")
            print(f"downloading s3://{src_bucket}/{src_key}")
            s3.download_file(src_bucket, src_key, archive_path)

            # Extract fully to local disk first — ZipFile handles aren't safe to
            # read from multiple threads — then parallelize the uploads.
            extract_dir = Path(tmp) / "extracted"
            with zipfile.ZipFile(archive_path) as zf:
                entries = [i for i in zf.infolist() if not i.is_dir()]
                print(f"extracting {len(entries)} entries -> s3://{bronze_bucket}/{prefix}")
                zf.extractall(extract_dir)
            os.remove(archive_path)

            def upload(info: zipfile.ZipInfo) -> dict:
                key = prefix + info.filename
                content_type = mimetypes.guess_type(info.filename)[0] or "application/octet-stream"
                s3.upload_file(
                    str(extract_dir / info.filename),
                    bronze_bucket,
                    key,
                    ExtraArgs={"ContentType": content_type},
                )
                return {
                    "key": key,
                    "size_bytes": info.file_size,
                    "crc32": info.CRC,
                }

            manifest_files = []
            with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
                for i, entry in enumerate(pool.map(upload, entries), 1):
                    manifest_files.append(entry)
                    if i % 1000 == 0:
                        print(f"  uploaded {i}/{len(entries)}")

        manifest = {
            "drop_id": drop_id,
            "partner": partner,
            "dataset": dataset,
            "ingest_date": ingest_date,
            "source": f"s3://{src_bucket}/{src_key}",
            "completed_at": datetime.now(UTC).isoformat(),
            "file_count": len(manifest_files),
            "total_bytes": sum(f["size_bytes"] for f in manifest_files),
            "files": manifest_files,
        }
        manifest_key = prefix + "_manifest.json"
        s3.put_object(
            Bucket=bronze_bucket,
            Key=manifest_key,
            Body=json.dumps(manifest).encode(),
            ContentType="application/json",
        )
        set_status(
            ddb,
            ledger_table,
            drop_id,
            "DONE",
            manifest_key=manifest_key,
            file_count=str(len(manifest_files)),
        )
        print(f"done: {len(manifest_files)} files, manifest at {manifest_key}")
        return 0
    except Exception as exc:
        set_status(ddb, ledger_table, drop_id, "FAILED", error=str(exc)[:1000])
        raise


if __name__ == "__main__":
    sys.exit(main())
