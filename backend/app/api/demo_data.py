from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.reporting_auth import ReportingPrincipal, principal
from app.services.demo_data_seeder import DemoDataUnavailable, clear_demo_data, demo_data_available, seed_demo_data

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


def _authorize(request: Request, identity: ReportingPrincipal) -> None:
    if "REPORT_ADMIN" not in identity.roles:
        raise HTTPException(status_code=403, detail="demo data requires reporting admin access")
    if not demo_data_available(request.app.state.settings):
        raise HTTPException(status_code=404, detail="demo data is available only for the local test merchant in Test Mode")


@router.get("/status")
def status(request: Request, identity: ReportingPrincipal = Depends(principal)) -> dict[str, bool]:
    _authorize(request, identity)
    return {"available": True}


@router.post("/data")
def generate(request: Request, identity: ReportingPrincipal = Depends(principal)) -> dict[str, object]:
    _authorize(request, identity)
    try:
        with request.app.state.session_factory() as session:
            with session.begin():
                result = seed_demo_data(session=session, settings=request.app.state.settings)
                return {"synthetic_demo": True, "generated": result.generated, "assessments": result.assessments, "advisories": result.advisories, "approved": result.approved, "denied": result.denied, "escalated": result.escalated, "execution_requests": result.execution_requests, "verified_revenue_minor_units": result.verified_revenue_minor_units}
    except DemoDataUnavailable as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/data")
def clear(request: Request, identity: ReportingPrincipal = Depends(principal)) -> dict[str, object]:
    _authorize(request, identity)
    with request.app.state.session_factory() as session:
        with session.begin():
            deleted = clear_demo_data(session=session, merchant_id=request.app.state.settings.merchant_id)
            return {"synthetic_demo": True, "deleted": deleted}
