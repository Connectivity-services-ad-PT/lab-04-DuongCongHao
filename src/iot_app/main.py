import os
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from fastapi import FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

SERVICE_NAME = os.getenv("SERVICE_NAME", "iot-ingestion")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "0.4.0")
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "local-dev-token")

app = FastAPI(
    title="FIT4110 Lab 04 - IoT Ingestion Service",
    version=SERVICE_VERSION,
    description="Dockerized IoT Ingestion API aligned with the Lab 03 OpenAPI/Postman contract.",
)

class SensorMetric(str, Enum):
    temperature = "temperature"
    humidity = "humidity"
    motion = "motion"
    smoke = "smoke"

class SensorUnit(str, Enum):
    celsius = "celsius"
    percent = "percent"
    boolean = "boolean"
    ppm = "ppm"

class ProblemDetails(BaseModel):
    type: str = "about:blank"
    title: str
    status: int = Field(..., ge=400, le=599)
    detail: str
    instance: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str

class SensorReadingCreate(BaseModel):
    device_id: str = Field(..., min_length=3, examples=["ESP32-LAB-A01"])
    metric: SensorMetric = Field(..., examples=["temperature"])
    value: float = Field(
        ...,
        ge=-40,
        le=80,
        description="Boundary range used in Lab 03 and Lab 04: -40 to 80.",
        examples=[31.5],
    )
    unit: Optional[SensorUnit] = Field(default=None, examples=["celsius"])
    timestamp: str = Field(..., examples=["2026-05-13T08:30:00+07:00"])

class SensorReadingCreated(BaseModel):
    reading_id: str
    device_id: str
    metric: SensorMetric
    accepted: bool
    created_at: str

READINGS: List[Dict] = []

def build_problem(
    *,
    status_code: int,
    title: str,
    detail: str,
    instance: Optional[str] = None,
    problem_type: str = "about:blank",
) -> Dict:
    problem = {
        "type": problem_type,
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    if instance:
        problem["instance"] = instance
    return problem

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(item) for item in first_error.get("loc", []))
    message = first_error.get("msg", "Request validation error")
    detail = f"{location}: {message}" if location else message

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=build_problem(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Validation error",
            detail=detail,
            instance=str(request.url.path),
            problem_type="https://smart-campus.local/problems/validation-error",
        ),
        media_type="application/problem+json",
    )

def check_authentication(request: Request) -> Optional[JSONResponse]:
    """Kiểm tra token thủ công an toàn để tránh crash 500"""
    authorization = request.headers.get("authorization") or request.headers.get("Authorization")
    
    if not authorization:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=build_problem(
                status_code=status.HTTP_401_UNAUTHORIZED,
                title="Unauthorized",
                detail="Missing Authorization header",
                problem_type="https://smart-campus.local/problems/unauthorized",
                instance=str(request.url.path)
            ),
            media_type="application/problem+json"
        )

    expected = f"Bearer {AUTH_TOKEN}"
    if authorization != expected:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=build_problem(
                status_code=status.HTTP_401_UNAUTHORIZED,
                title="Unauthorized",
                detail="Invalid bearer token",
                problem_type="https://smart-campus.local/problems/unauthorized",
                instance=str(request.url.path)
            ),
            media_type="application/problem+json"
        )
    return None

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def next_reading_id() -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"R-{today}-{len(READINGS) + 1:04d}"

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
    )

@app.post(
    "/readings",
    response_model=SensorReadingCreated,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def create_reading(
    request: Request, 
    payload: SensorReadingCreate,  # Trả payload về tham số chuẩn để FastAPI tự động bắt lỗi dữ liệu đầu vào và trả về 422
    response: Response
) -> Response:
    # 1. Xác thực Auth trước bằng Request
    auth_error = check_authentication(request)
    if auth_error:
        return auth_error

    # 2. Nếu đi đến đây, dữ liệu 'payload' đã được FastAPI tự động kiểm tra (validate) thành công.
    # Logic kiểm tra cảnh báo nhiệt độ cao
    if payload.metric == SensorMetric.temperature and payload.value >= 70:
        response.headers["X-Warning"] = "high-temperature"

    reading_id = next_reading_id()
    created_at = now_iso()

    item = {
        "reading_id": reading_id,
        "device_id": payload.device_id,
        "metric": payload.metric.value,
        "value": payload.value,
        "unit": payload.unit.value if payload.unit else None,
        "timestamp": payload.timestamp,
        "created_at": created_at,
    }
    READINGS.append(item)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "reading_id": reading_id,
            "device_id": payload.device_id,
            "metric": payload.metric.value,
            "accepted": True,
            "created_at": created_at,
        },
        headers=dict(response.headers)
    )

@app.get("/readings/latest")
def latest_readings(
    request: Request,
    device_id: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
):
    auth_error = check_authentication(request)
    if auth_error:
        return auth_error

    items = READINGS
    if device_id:
        items = [item for item in items if item["device_id"] == device_id]

    return {"items": items[-limit:]}

@app.get("/readings/{reading_id}")
def get_reading(request: Request, reading_id: str):
    auth_error = check_authentication(request)
    if auth_error:
        return auth_error

    for item in READINGS:
        if item["reading_id"] == reading_id:
            return item

    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=build_problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"Reading {reading_id} does not exist",
            instance=f"/readings/{reading_id}",
            problem_type="https://smart-campus.local/problems/not-found",
        ),
        media_type="application/problem+json"
    )