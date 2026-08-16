FROM python:3.12-slim

RUN pip install --no-cache-dir boto3 pandas pyarrow

WORKDIR /app
COPY gold_builder.py .

CMD ["python", "gold_builder.py"]
