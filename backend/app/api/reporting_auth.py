from __future__ import annotations
from dataclasses import dataclass
from fastapi import Header, HTTPException, Request
@dataclass(frozen=True)
class ReportingPrincipal: merchant_id:str; roles:frozenset[str]
def principal(request:Request,x_recover_merchant:str|None=Header(default=None),x_recover_role:str|None=Header(default=None))->ReportingPrincipal:
    settings=request.app.state.settings
    if not settings.reporting_auth_required:return ReportingPrincipal(settings.merchant_id,frozenset({"REPORT_ADMIN"}))
    if not x_recover_merchant or x_recover_role not in ("REPORT_VIEWER","REPORT_ADMIN"):raise HTTPException(401,"reporting principal required")
    return ReportingPrincipal(x_recover_merchant,frozenset({x_recover_role}))
