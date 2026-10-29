"""Send scan jobs to Amazon SQS."""

import json
from typing import Any

import boto3

from config import settings


def enqueue_scan_job(job: dict[str, Any]) -> str:
    """Enqueue a scan job and return its SQS message ID."""
    if not settings.SQS_QUEUE_URL:
        raise RuntimeError("SQS_QUEUE_URL is not configured")

    client = boto3.client(
        "sqs",
        region_name=settings.AWS_REGION,
    )

    response = client.send_message(
        QueueUrl=settings.SQS_QUEUE_URL,
        MessageBody=json.dumps(job),
    )

    return response["MessageId"]
