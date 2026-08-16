FROM python:3.12-slim

RUN pip install --no-cache-dir boto3

WORKDIR /app
COPY main.py .

ENTRYPOINT ["python", "main.py"]
