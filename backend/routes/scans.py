"""
©AngelaMos | 2025
Scan routes - create, retrieve, and manage security scans
"""
from fastapi.concurrency import run_in_threadpool
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from config import settings
from core.database import get_db
from core.dependencies import get_current_user
from schemas.scan_schemas import (
    ScanRequest,
    ScanResponse,
)
from schemas.user_schemas import UserResponse
from services.scan_service import ScanService

from repositories.scan_repository import ScanRepository
from services.scan_queue import enqueue_scan_job

router = APIRouter(prefix="/scans", tags=["scans"])
limiter = Limiter(key_func=get_remote_address)

def _create_queued_scan(
    db: Session,
    user_id: int,
    scan_request: ScanRequest,
) -> ScanResponse:
    """Create a scan record and enqueue its job."""
    scan = ScanRepository.create_scan(
        db=db,
        user_id=user_id,
        target_url=str(scan_request.target_url),
    )

    scan.status = "queued"
    db.commit()
    db.refresh(scan)
    scan_id = scan.id

    try:
        enqueue_scan_job({
            "scan_id": scan_id,
            "user_id": user_id,
            "scan_request": scan_request.model_dump(mode="json"),
        })
    except Exception as exc:
        db.rollback()
        failed_scan = ScanRepository.get_by_id(db, scan_id)

        if failed_scan is not None:
            failed_scan.status = "failed"
            db.commit()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to queue scan job",
        ) from exc

    db.refresh(scan)
    return ScanResponse.model_validate(scan)

@router.post(
    "/",
    response_model=ScanResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(settings.API_RATE_LIMIT_SCAN)
async def create_scan(
    request: Request,
    scan_request: ScanRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> ScanResponse:
    """
    Create a scan and enqueue it for background processing.
    """
    return await run_in_threadpool(
        _create_queued_scan,
        db,
        current_user.id,
        scan_request,
    )


@router.get(
    "/",
    response_model=list[ScanResponse],
    status_code=status.HTTP_200_OK,
)
@limiter.limit(settings.API_RATE_LIMIT_DEFAULT)
async def get_user_scans(
    request: Request,
    skip: int = 0,
    limit: int | None = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> list[ScanResponse]:
    """
    Get all scans for the authenticated user
    """
    return ScanService.get_user_scans(db, current_user.id, skip, limit)


@router.get(
    "/{scan_id}",
    response_model=ScanResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit(settings.API_RATE_LIMIT_DEFAULT)
async def get_scan(
    request: Request,
    scan_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> ScanResponse:
    """
    Get a specific scan by ID
    """
    return ScanService.get_scan_by_id(db, scan_id, current_user.id)


@router.delete(
    "/{scan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit(settings.API_RATE_LIMIT_DEFAULT)
async def delete_scan(
    request: Request,
    scan_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> None:
    """
    Delete a scan by ID
    """
    ScanService.delete_scan(db, scan_id, current_user.id)
