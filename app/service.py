from __future__ import annotations

from typing import Any

from .core import AssessmentResult, assess
from .parsing import parse_assessment_request


def build_success_body(result: AssessmentResult, request) -> dict[str, Any]:
    body = result.as_dict()
    body["row_labels"] = list(request.row_labels or ())
    body["column_labels"] = list(request.column_labels or ())
    body["request_note"] = (
        "row/column 为从 0 开始的数组下标；row_number/column_number 为从 1 开始的编号。"
    )
    return body


def calculate(payload: Any) -> dict[str, Any]:
    """Parse a raw JSON-like payload, assess it, and return a response dict.

    Structured ``AssessmentError`` subclasses are raised for conflict and
    measurement rejection so HTTP and Python callers can handle them.
    """
    request = parse_assessment_request(payload)
    result = assess(request)
    return build_success_body(result, request)
