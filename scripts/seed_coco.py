"""Simulate the COCO partner: download COCO archives and drop them in the
landing bucket as-delivered (no platform key scheme — that's applied at bronze).

Usage: python scripts/seed_coco.py [--profile image-platform]
Downloads are cached in data/ and both steps are idempotent.
"""

import argparse
import sys
import urllib.request
from pathlib import Path

import boto3

LANDING_BUCKET = "imgp-dev-landing-coco-935961368629"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ARCHIVES = {
    "val2017.zip": "http://images.cocodataset.org/zips/val2017.zip",
    "annotations_trainval2017.zip": "http://images.cocodataset.org/annotations/annotations_trainval2017.zip",
}


def download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url) as resp:
        expected = int(resp.headers.get("Content-Length", 0))
        if dest.exists() and expected and dest.stat().st_size == expected:
            print(f"  cached: {dest.name} ({expected / 1e6:.0f} MB)")
            return
        tmp = dest.with_suffix(".part")
        done = 0
        with tmp.open("wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if done % (100 << 20) < (1 << 20):
                    print(f"  {dest.name}: {done / 1e6:.0f} / {expected / 1e6:.0f} MB")
        tmp.rename(dest)
        print(f"  downloaded: {dest.name} ({done / 1e6:.0f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="image-platform")
    args = parser.parse_args()

    s3 = boto3.Session(profile_name=args.profile).client("s3")
    DATA_DIR.mkdir(exist_ok=True)

    for name, url in ARCHIVES.items():
        print(f"fetching {name}")
        download(url, DATA_DIR / name)

    for name in ARCHIVES:
        key = f"drops/{name}"
        local = DATA_DIR / name
        try:
            head = s3.head_object(Bucket=LANDING_BUCKET, Key=key)
            if head["ContentLength"] == local.stat().st_size:
                print(f"already in landing: s3://{LANDING_BUCKET}/{key}")
                continue
        except s3.exceptions.ClientError:
            pass
        print(f"uploading s3://{LANDING_BUCKET}/{key}")
        s3.upload_file(str(local), LANDING_BUCKET, key)

    print("seed complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
