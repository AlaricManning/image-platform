"""image-profile Batch job: for every image listed in a bronze manifest,
extract metadata (dimensions, format, EXIF basics, sha256) and generate a
thumbnail. Writes to the silver staging zone (the EMR Spark job merges
staging into the Iceberg tables):

  staging/image_metadata/partner=/dataset=/ingest_date=/part-00000.parquet
  thumbnails/partner=/dataset=/ingest_date=/<file>.jpg
"""

import hashlib
import io
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

WORKERS = 16
THUMBNAIL_MAX = 256
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")

EXIF_MAKE, EXIF_MODEL, EXIF_ORIENTATION = 271, 272, 274

# Explicit schema so all-null columns (e.g. EXIF fields on COCO images) don't
# get inferred as parquet null type, which Athena can't read.
SCHEMA = pa.schema(
    [
        ("partner", pa.string()),
        ("dataset", pa.string()),
        ("ingest_date", pa.string()),
        ("bronze_key", pa.string()),
        ("file_name", pa.string()),
        ("size_bytes", pa.int64()),
        ("sha256", pa.string()),
        ("width", pa.int64()),
        ("height", pa.int64()),
        ("format", pa.string()),
        ("mode", pa.string()),
        ("exif_make", pa.string()),
        ("exif_model", pa.string()),
        ("exif_orientation", pa.int64()),
        ("thumbnail_key", pa.string()),
        ("error", pa.string()),
    ]
)


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required env var {name}")
    return value


def main() -> int:
    bronze_bucket = env("BRONZE_BUCKET")
    manifest_key = env("MANIFEST_KEY")
    silver_bucket = env("SILVER_BUCKET")

    s3 = boto3.client("s3")
    manifest = json.loads(s3.get_object(Bucket=bronze_bucket, Key=manifest_key)["Body"].read())
    partner = manifest["partner"]
    dataset = manifest["dataset"]
    ingest_date = manifest["ingest_date"]
    partition = f"partner={partner}/dataset={dataset}/ingest_date={ingest_date}/"

    image_keys = [f["key"] for f in manifest["files"] if f["key"].lower().endswith(IMAGE_SUFFIXES)]
    print(f"profiling {len(image_keys)} images from {manifest_key}")

    def profile(key: str) -> dict:
        body = s3.get_object(Bucket=bronze_bucket, Key=key)["Body"].read()
        file_name = key.rsplit("/", 1)[-1]
        row = {
            "partner": partner,
            "dataset": dataset,
            "ingest_date": ingest_date,
            "bronze_key": key,
            "file_name": file_name,
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "width": None,
            "height": None,
            "format": None,
            "mode": None,
            "exif_make": None,
            "exif_model": None,
            "exif_orientation": None,
            "thumbnail_key": None,
            "error": None,
        }
        try:
            with Image.open(io.BytesIO(body)) as img:
                row["width"], row["height"] = img.size
                row["format"] = img.format
                row["mode"] = img.mode
                exif = img.getexif()
                if exif:
                    row["exif_make"] = str(exif.get(EXIF_MAKE)) if exif.get(EXIF_MAKE) else None
                    row["exif_model"] = str(exif.get(EXIF_MODEL)) if exif.get(EXIF_MODEL) else None
                    orientation = exif.get(EXIF_ORIENTATION)
                    row["exif_orientation"] = int(orientation) if orientation else None

                thumb = img.copy()
                thumb.thumbnail((THUMBNAIL_MAX, THUMBNAIL_MAX))
                if thumb.mode not in ("RGB", "L"):
                    thumb = thumb.convert("RGB")
                buf = io.BytesIO()
                thumb.save(buf, "JPEG", quality=85)
                thumb_key = f"thumbnails/{partition}{file_name.rsplit('.', 1)[0]}.jpg"
                s3.put_object(
                    Bucket=silver_bucket,
                    Key=thumb_key,
                    Body=buf.getvalue(),
                    ContentType="image/jpeg",
                )
                row["thumbnail_key"] = thumb_key
        except Exception as exc:  # noqa: BLE001 - record and continue
            row["error"] = str(exc)[:500]
        return row

    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, row in enumerate(pool.map(profile, image_keys), 1):
            rows.append(row)
            if i % 1000 == 0:
                print(f"  profiled {i}/{len(image_keys)}")

    errors = sum(1 for r in rows if r["error"])
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        pq.write_table(table, tmp.name)
        out_key = f"staging/image_metadata/{partition}part-00000.parquet"
        s3.upload_file(tmp.name, silver_bucket, out_key)

    print(f"done: {len(rows)} rows ({errors} errors) -> s3://{silver_bucket}/{out_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
