"""normalize-annotations Batch job: turn COCO annotation JSONs from a bronze
manifest into tidy parquet tables in silver, partitioned by split (train2017,
val2017, ...):

  staging/coco_images/<partition>/split=<split>/part-00000.parquet
  staging/coco_categories/<partition>/split=<split>/part-00000.parquet
  staging/coco_annotations/<partition>/split=<split>/part-00000.parquet   (instances)
  staging/coco_captions/<partition>/split=<split>/part-00000.parquet     (captions)

person_keypoints files are skipped for now (keypoint arrays need their own
modeling; revisit when a product needs them).
"""

import json
import os
import sys
import tempfile

import boto3
import pyarrow as pa
import pyarrow.parquet as pq


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required env var {name}")
    return value


def write_table(s3, bucket: str, key: str, rows: list[dict]):
    table = pa.Table.from_pylist(rows)
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        pq.write_table(table, tmp.name)
        s3.upload_file(tmp.name, bucket, key)
    print(f"  wrote {len(rows)} rows -> s3://{bucket}/{key}")


def main() -> int:
    bronze_bucket = env("BRONZE_BUCKET")
    manifest_key = env("MANIFEST_KEY")
    silver_bucket = env("SILVER_BUCKET")

    s3 = boto3.client("s3")
    manifest = json.loads(s3.get_object(Bucket=bronze_bucket, Key=manifest_key)["Body"].read())
    partition = (
        f"partner={manifest['partner']}/dataset={manifest['dataset']}"
        f"/ingest_date={manifest['ingest_date']}/"
    )
    # Iceberg partitions on real columns, so every row carries these fields
    # (the staging path layout is just for humans now, not a schema contract).
    base = {
        "partner": manifest["partner"],
        "dataset": manifest["dataset"],
        "ingest_date": manifest["ingest_date"],
    }

    for entry in manifest["files"]:
        key = entry["key"]
        file_name = key.rsplit("/", 1)[-1]
        stem = file_name.rsplit(".", 1)[0]
        kind, _, split = stem.partition("_")

        if kind == "person" or not split:
            if file_name.startswith("person_keypoints_"):
                print(f"skipping keypoints file: {file_name}")
            continue
        if kind not in ("instances", "captions"):
            continue

        print(f"loading {file_name}")
        doc = json.loads(s3.get_object(Bucket=bronze_bucket, Key=key)["Body"].read())
        split_part = f"{partition}split={split}/part-00000.parquet"

        if kind == "instances":
            write_table(
                s3,
                silver_bucket,
                f"staging/coco_images/{split_part}",
                [
                    {
                        **base,
                        "image_id": img["id"],
                        "file_name": img["file_name"],
                        "width": img["width"],
                        "height": img["height"],
                        "split": split,
                    }
                    for img in doc["images"]
                ],
            )
            write_table(
                s3,
                silver_bucket,
                f"staging/coco_categories/{split_part}",
                [
                    {
                        **base,
                        "category_id": c["id"],
                        "name": c["name"],
                        "supercategory": c["supercategory"],
                        "split": split,
                    }
                    for c in doc["categories"]
                ],
            )
            write_table(
                s3,
                silver_bucket,
                f"staging/coco_annotations/{split_part}",
                [
                    {
                        **base,
                        "annotation_id": a["id"],
                        "image_id": a["image_id"],
                        "category_id": a["category_id"],
                        "bbox_x": a["bbox"][0],
                        "bbox_y": a["bbox"][1],
                        "bbox_w": a["bbox"][2],
                        "bbox_h": a["bbox"][3],
                        "area": a["area"],
                        "iscrowd": a["iscrowd"],
                        "split": split,
                    }
                    for a in doc["annotations"]
                ],
            )
        elif kind == "captions":
            write_table(
                s3,
                silver_bucket,
                f"staging/coco_captions/{split_part}",
                [
                    {
                        **base,
                        "caption_id": a["id"],
                        "image_id": a["image_id"],
                        "caption": a["caption"],
                        "split": split,
                    }
                    for a in doc["annotations"]
                ],
            )
        del doc

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
