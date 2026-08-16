"""gold-builder Batch job: joins silver tables into the first gold products
for one partner/split:

  tables/category_stats/partner=/dataset=/ingest_date=/part-00000.parquet
      per category: annotation/image counts, avg bbox area, crowd count
  tables/ml_manifest/partner=/dataset=/ingest_date=/part-00000.parquet
      per image: bronze/thumbnail URIs, dimensions, checksum, label summary

Run manually (or by future orchestration):
  aws batch submit-job --job-definition imgp-dev-gold-builder ...
"""

import os
import sys
import tempfile
from pathlib import Path

import boto3
import pandas as pd


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required env var {name}")
    return value


def read_prefix(s3, bucket: str, prefix: str, tmp: str) -> pd.DataFrame:
    """Read all parquet parts under an s3 prefix into one dataframe."""
    keys = [
        obj["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".parquet")
    ]
    if not keys:
        raise SystemExit(f"no parquet under s3://{bucket}/{prefix}")
    frames = []
    for i, key in enumerate(keys):
        local = Path(tmp) / f"in-{i}.parquet"
        s3.download_file(bucket, key, str(local))
        frames.append(pd.read_parquet(local))
        local.unlink()
    return pd.concat(frames, ignore_index=True)


def write_gold(s3, df: pd.DataFrame, bucket: str, table: str, partition: str):
    with tempfile.NamedTemporaryFile(suffix=".parquet") as out:
        df.to_parquet(out.name, index=False)
        key = f"tables/{table}/{partition}part-00000.parquet"
        s3.upload_file(out.name, bucket, key)
    print(f"wrote {len(df)} rows -> s3://{bucket}/tables/{table}/{partition}")


def main() -> int:
    silver_bucket = env("SILVER_BUCKET")
    gold_bucket = env("GOLD_BUCKET")
    partner = env("PARTNER")
    images_dataset = env("IMAGES_DATASET")
    annotations_dataset = env("ANNOTATIONS_DATASET")
    split = env("SPLIT")
    ingest_date = env("INGEST_DATE")

    s3 = boto3.client("s3")
    img_part = f"partner={partner}/dataset={images_dataset}/ingest_date={ingest_date}/"
    ann_part = (
        f"partner={partner}/dataset={annotations_dataset}/ingest_date={ingest_date}/split={split}/"
    )

    with tempfile.TemporaryDirectory() as tmp:
        metadata = read_prefix(s3, silver_bucket, f"tables/image_metadata/{img_part}", tmp)
        images = read_prefix(s3, silver_bucket, f"tables/coco_images/{ann_part}", tmp)
        categories = read_prefix(s3, silver_bucket, f"tables/coco_categories/{ann_part}", tmp)
        annotations = read_prefix(s3, silver_bucket, f"tables/coco_annotations/{ann_part}", tmp)

    ann = annotations.merge(
        categories[["category_id", "name", "supercategory"]], on="category_id", how="left"
    )

    category_stats = (
        ann.groupby(["category_id", "name", "supercategory"], as_index=False)
        .agg(
            n_annotations=("annotation_id", "count"),
            n_images=("image_id", "nunique"),
            avg_area=("area", "mean"),
            crowd_annotations=("iscrowd", "sum"),
        )
        .rename(columns={"name": "category"})
        .sort_values("n_annotations", ascending=False, ignore_index=True)
    )

    per_image = ann.groupby("image_id", as_index=False).agg(
        n_annotations=("annotation_id", "count"),
        n_categories=("name", "nunique"),
        categories=("name", lambda s: ",".join(sorted(set(s)))),
    )

    ml_manifest = (
        images[["image_id", "file_name", "width", "height"]]
        .merge(
            metadata[["file_name", "bronze_key", "thumbnail_key", "size_bytes", "sha256"]],
            on="file_name",
            how="inner",
        )
        .merge(per_image, on="image_id", how="left")
    )
    ml_manifest["n_annotations"] = ml_manifest["n_annotations"].fillna(0).astype("int64")
    ml_manifest["n_categories"] = ml_manifest["n_categories"].fillna(0).astype("int64")
    ml_manifest["categories"] = ml_manifest["categories"].fillna("")

    gold_partition = f"partner={partner}/dataset={images_dataset}/ingest_date={ingest_date}/"
    write_gold(s3, category_stats, gold_bucket, "category_stats", gold_partition)
    write_gold(s3, ml_manifest, gold_bucket, "ml_manifest", gold_partition)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
