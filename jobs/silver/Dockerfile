FROM python:3.12-slim

RUN pip install --no-cache-dir boto3 pillow pyarrow

WORKDIR /app
COPY image_profile.py normalize_annotations.py ./

# Command comes from the Batch job definition:
#   ["python", "image_profile.py"] or ["python", "normalize_annotations.py"]
CMD ["python", "image_profile.py"]
