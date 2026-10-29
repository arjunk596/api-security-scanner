"""Store scan reports in the private S3 reports bucket."""

import json
from typing import Any

import boto3

from config import settings


def upload_scan_report(
    scan_id: int,
    user_id: int,
    report: dict[str, Any],
) -> str:
    """Upload a JSON scan report and return its S3 object key."""
    if not settings.REPORTS_BUCKET:
        raise RuntimeError("REPORTS_BUCKET is not configured")

    key = f"reports/user-{user_id}/scan-{scan_id}.json"

    client = boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
    )

    client.put_object(
        Bucket=settings.REPORTS_BUCKET,
        Key=key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )

    return key
