from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .core import (
    AssessmentError,
    MissingMeasurementError,
    RequestDataError,
    ScheduleConflictError,
)
from .service import calculate

app = FastAPI(
    title="公交考核排班结构加权核算服务",
    description=(
        "提交行列合计、少数已知格班次数以及两期各格实测/准点班次；"
        "枚举所有符合排班记录的非负整数表对应的线性极值，返回精确分数与见证表。"
    ),
    version=__version__,
)


def _error_content(exc: AssessmentError) -> dict:
    content: dict = {
        "status": exc.code,
        "message": exc.message,
    }
    if isinstance(exc, MissingMeasurementError):
        content["blocked_cells"] = exc.blocked_cells
        # Explicit alias for clients using the longer descriptive name.
        content["missing_measurement_cells"] = exc.blocked_cells
        content["result_available"] = False
    elif isinstance(exc, ScheduleConflictError):
        content["conflicts"] = exc.details
        content["details"] = exc.details
        content["feasible"] = False
    elif exc.details:
        content["details"] = exc.details
    return content


@app.exception_handler(AssessmentError)
async def assessment_error_handler(request: Request, exc: AssessmentError) -> JSONResponse:
    status_code = 200 if request.query_params.get("always_200") in {"1", "true", "yes"} else exc.http_status
    return JSONResponse(status_code=status_code, content=_error_content(exc))


@app.get("/health")
@app.get("/healthz")
async def health() -> dict:
    return {"status": "ok", "service": "shift-structure-assessment", "version": __version__}


@app.get("/")
async def root_info() -> dict:
    return {
        "service": "shift-structure-assessment",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "endpoints": [
            "POST /api/v1/assessments",
            "POST /api/v1/evaluate",
            "POST /evaluate",
        ],
    }


@app.post("/api/v1/assessments", summary="核算排班结构影响的准点率差极值")
@app.post("/api/v1/shift-assessments", include_in_schema=False)
@app.post("/api/v1/evaluate", include_in_schema=False)
@app.post("/api/v1/calculate", include_in_schema=False)
@app.post("/api/assessments", include_in_schema=False)
@app.post("/api/assess", include_in_schema=False)
@app.post("/api/evaluate", include_in_schema=False)
@app.post("/api/calculate", include_in_schema=False)
@app.post("/assessments", include_in_schema=False)
@app.post("/assess", include_in_schema=False)
@app.post("/calculate", include_in_schema=False)
@app.post("/evaluate", include_in_schema=False)
@app.post("/", include_in_schema=False)
async def evaluate(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception as exc:  # FastAPI normally handles malformed JSON; keep a clear body.
        raise RequestDataError("请求体不是合法的 JSON 对象。") from exc

    body = calculate(payload)

    # Optional envelope mode for clients that prefer business outcomes with
    # HTTP 200. Standard calls use 409/422 for rejection scenarios.
    return JSONResponse(status_code=200, content=body)
