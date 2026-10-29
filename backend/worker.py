"""Background worker for queued security scans."""

import json
import time

import boto3
from sqlalchemy.exc import SQLAlchemyError

from config import settings
from core.database import SessionLocal
from repositories.scan_repository import ScanRepository
from schemas.scan_schemas import ScanRequest
from services.scan_service import ScanService


def main() -> None:
    if not settings.SQS_QUEUE_URL:
        raise RuntimeError("SQS_QUEUE_URL is not configured")

    sqs = boto3.client(
        "sqs",
        region_name=settings.AWS_REGION,
    )

    print("Scan worker started.")

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=settings.SQS_QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=1800,
		MessageSystemAttributeNames=["ApproximateReceiveCount"],
            )

            for message in response.get("Messages", []):
                process_message(sqs, message)

        except Exception as exc:
            print(f"SQS polling error: {type(exc).__name__}")
            time.sleep(5)


def process_message(sqs, message: dict) -> None:
    db = SessionLocal()
    scan_id = None

    try:
        job = json.loads(message["Body"])
        scan_id = int(job["scan_id"])
        user_id = int(job["user_id"])
        scan_request = ScanRequest.model_validate(
            job["scan_request"]
        )

        scan = ScanRepository.get_by_id(db, scan_id)

        if scan is None or scan.status == "completed":
            sqs.delete_message(
                QueueUrl=settings.SQS_QUEUE_URL,
                ReceiptHandle=message["ReceiptHandle"],
            )
            return

        ScanService.run_scan(
            db=db,
            user_id=user_id,
            scan_request=scan_request,
            scan_id=scan_id,
        )

        sqs.delete_message(
            QueueUrl=settings.SQS_QUEUE_URL,
            ReceiptHandle=message["ReceiptHandle"],
        )

        print(f"Scan {scan_id} completed.")

    except Exception as exc:
        db.rollback()
        receive_count = int(
            message.get("Attributes", {}).get(
                "ApproximateReceiveCount", "1"
            )
        )

        if scan_id is not None:
            try:
                scan = ScanRepository.get_by_id(db, scan_id)
                if scan is not None and scan.status != "completed":
                    scan.status = (
                        "failed" if receive_count >= 3 else "queued"
                    )
                    db.commit()
            except SQLAlchemyError:
                db.rollback()

        print(
            f"Scan job attempt {receive_count} failed: "
            f"{type(exc).__name__}. "
            "SQS will retry or move the message to the DLQ."
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
