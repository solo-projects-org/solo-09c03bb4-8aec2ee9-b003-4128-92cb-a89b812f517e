from __future__ import annotations

import re
from typing import Any

from .core import (
    MAX_DIM,
    MAX_TOTAL,
    MIN_TOTAL,
    AssessmentRequest,
    PeriodMeasurements,
    RequestDataError,
    ScheduleConflictError,
)


def norm_key(key: Any) -> str:
    if not isinstance(key, str):
        return str(key)
    return re.sub(r"[\s_\-]+", "", key).lower()


def _lookup_in_dict(obj: dict[str, Any], aliases: list[str]) -> tuple[bool, Any]:
    normalized = {norm_key(k): v for k, v in obj.items()}
    for alias in aliases:
        key = norm_key(alias)
        if key in normalized:
            return True, normalized[key]
    return False, None


def find_value(
    sources: list[dict[str, Any]], aliases: list[str]
) -> tuple[bool, Any, dict[str, Any] | None]:
    for source in sources:
        if not isinstance(source, dict):
            continue
        found, value = _lookup_in_dict(source, aliases)
        if found:
            return True, value, source
    return False, None, None


def require_value(sources: list[dict[str, Any]], aliases: list[str], description: str) -> Any:
    found, value, _ = find_value(sources, aliases)
    if not found:
        raise RequestDataError(
            f"缺少必填字段：{description}",
            [{"code": "missing_field", "aliases": aliases}],
        )
    return value


def as_index(value: Any, dimension: int, kind: str, one_based: bool = False) -> int:
    if isinstance(value, bool):
        raise RequestDataError(f"{kind}索引不能是布尔值。", [{"value": value}])
    if isinstance(value, int):
        index = value - 1 if one_based else value
    elif isinstance(value, float) and value.is_integer():
        index = int(value) - (1 if one_based else 0)
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        index = int(value.strip()) - (1 if one_based else 0)
    else:
        raise RequestDataError(
            f"{kind}索引必须是 0 基整数{f'或 1 基编号' if one_based else ''}。",
            [{"code": "invalid_index", "value": value}],
        )
    if not 0 <= index < dimension:
        raise RequestDataError(
            f"{kind}索引超出范围 0..{dimension - 1}。",
            [{"code": "index_out_of_range", "value": value, "resolved_index": index, "dimension": dimension}],
        )
    return index


def resolve_coordinate(
    record: dict[str, Any],
    coordinate: str,
    dimension: int,
    labels: tuple[str, ...] | None,
    default_one_based: bool = False,
) -> int:
    if coordinate == "row":
        explicit_zero_aliases = ["row_index", "rowindex", "row_idx", "i", "r"]
        coordinate_aliases = ["row"]
        number_aliases = ["row_number", "rownumber", "row_no", "rowno", "row_num", "hanghao"]
        label_aliases = ["row_label", "rowlabel", "row_name", "rowname", "route_category", "routecategory", "route", "line", "line_category", "linecategory"]
        kind = "行"
    else:
        explicit_zero_aliases = [
            "column_index",
            "columnindex",
            "column_idx",
            "col_index",
            "colindex",
            "col_idx",
            "j",
            "c",
        ]
        coordinate_aliases = ["column", "col"]
        number_aliases = ["column_number", "columnnumber", "col_number", "colnumber", "column_no", "col_no", "colno", "column_num", "colnum"]
        label_aliases = ["column_label", "columnlabel", "column_name", "columnname", "time_period", "timeperiod", "period", "slot", "timeslot", "time_slot"]
        kind = "列"

    found, value, _source = find_value([record], label_aliases)
    if found and isinstance(value, str) and labels and value in labels:
        return labels.index(value)

    found, value, _source = find_value([record], number_aliases)
    if found:
        return as_index(value, dimension, kind, one_based=True)

    found, value, _source = find_value([record], explicit_zero_aliases)
    if found:
        # A string that exactly equals a label takes priority over numeric form.
        if isinstance(value, str) and labels and value in labels:
            return labels.index(value)
        return as_index(value, dimension, kind, one_based=False)

    found, value, _source = find_value([record], coordinate_aliases)
    if found:
        if isinstance(value, str) and labels and value in labels:
            return labels.index(value)
        return as_index(value, dimension, kind, one_based=default_one_based)

    raise RequestDataError(
        f"固定格记录缺少有效的{kind}坐标。",
        [{"code": "missing_coordinate", "coordinate": coordinate, "record": record}],
    )


def parse_labels(value: Any, dimension: int, kind: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise RequestDataError(f"{kind}标签必须是数组。", [{"code": "invalid_labels", "value": value}])
    if len(value) != dimension:
        raise RequestDataError(
            f"{kind}标签数量必须为 {dimension}。",
            [{"code": "label_length_mismatch", "expected": dimension, "actual": len(value)}],
        )
    labels = []
    for item in value:
        if isinstance(item, str):
            labels.append(item)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            labels.append(str(item))
        elif isinstance(item, dict):
            found, name, _source = find_value(
                [item],
                [
                    "label",
                    "name",
                    "title",
                    "key",
                    "category",
                    "row_label",
                    "column_label",
                    "route_category",
                    "time_period",
                    "route",
                    "line",
                    "period",
                ],
            )
            if not found or not isinstance(name, str):
                raise RequestDataError(f"{kind}标签对象缺少 name/label。", [{"item": item}])
            labels.append(name)
        else:
            raise RequestDataError(f"无法识别的{kind}标签。", [{"item": item}])
    return tuple(labels)


def parse_total_vector(value: Any, kind: str) -> tuple[tuple[int, ...], tuple[str, ...] | None]:
    labels: tuple[str, ...] | None = None

    if isinstance(value, dict):
        labels = tuple(str(k) for k in value.keys())
        raw_values = list(value.values())
    elif isinstance(value, list):
        raw_values = value
    else:
        raise RequestDataError(f"{kind}合计必须是非负整数数组。", [{"code": "invalid_totals", "value": value}])

    result: list[int] = []
    for item in raw_values:
        if isinstance(item, dict):
            found, nested, _source = find_value(
                [item],
                ["total", "total_shifts", "shifts", "count", "value", "row_total", "column_total", "banci"],
            )
            if not found:
                raise RequestDataError(f"{kind}合计对象缺少 total/shifts/count。", [{"item": item}])
            item = nested
        if isinstance(item, bool) or not isinstance(item, int):
            if isinstance(item, float) and item.is_integer():
                item = int(item)
            else:
                raise RequestDataError(f"{kind}合计必须全部为非负整数。", [{"value": item}])
        if item < 0:
            raise RequestDataError(f"{kind}合计不能为负数。", [{"value": item}])
        result.append(item)

    if not 1 <= len(result) <= MAX_DIM:
        raise RequestDataError(
            f"{kind}类别数必须在 1 到 {MAX_DIM} 之间。",
            [{"code": "invalid_dimension", "actual": len(result)}],
        )
    return tuple(result), labels


def parse_optional_int_cell(value: Any, field_name: str, allow_none: bool) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise RequestDataError(f"{field_name}不能是布尔值。", [{"value": value}])
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        number = int(value.strip())
    else:
        if allow_none:
            return None
        raise RequestDataError(f"{field_name}必须是非负整数。", [{"value": value}])
    if number < 0:
        raise RequestDataError(f"{field_name}不能为负数。", [{"value": value}])
    return number


def parse_matrix(
    value: Any,
    m: int,
    n: int,
    field_name: str,
    *,
    allow_none: bool,
) -> tuple[tuple[int | None, ...], ...]:
    if isinstance(value, list) and len(value) == m * n and not any(isinstance(x, list) for x in value):
        rows = [value[i * n : (i + 1) * n] for i in range(m)]
    elif isinstance(value, list):
        rows = value
    else:
        raise RequestDataError(f"{field_name}必须是 {m}×{n} 的二维数组。", [{"code": "matrix_not_array"}])

    if len(rows) != m:
        raise RequestDataError(
            f"{field_name}行数应为 {m}。",
            [{"code": "matrix_row_mismatch", "expected": m, "actual": len(rows)}],
        )

    result = []
    for i, row in enumerate(rows):
        if not isinstance(row, list):
            raise RequestDataError(f"{field_name}第 {i + 1} 行必须是数组。", [{"row": row}])
        if len(row) != n:
            raise RequestDataError(
                f"{field_name}第 {i + 1} 行列数应为 {n}。",
                [{"code": "matrix_column_mismatch", "row": i, "expected": n, "actual": len(row)}],
            )
        parsed_row = tuple(
            parse_optional_int_cell(cell, f"{field_name}[{i}][{j}]", allow_none)
            for j, cell in enumerate(row)
        )
        result.append(parsed_row)
    return tuple(result)


def find_period_object(payload: dict[str, Any], aliases: list[str]) -> dict[str, Any] | list[Any] | None:
    sources: list[dict[str, Any]] = [payload]
    for container_key in ["periods", "data", "measurement_periods", "两期数据"]:
        container = payload.get(container_key)
        if isinstance(container, dict):
            sources.append(container)

    found, value, _ = find_value(sources, aliases)
    if found and isinstance(value, (dict, list)):
        return value

    # Flattened keys such as baseline_measured_shifts are handled by callers.
    return None


def parse_period(
    payload: dict[str, Any],
    period_aliases: list[str],
    prefix_aliases: list[str],
    m: int,
    n: int,
    period_label: str,
) -> PeriodMeasurements:
    period_obj = find_period_object(payload, period_aliases)

    measured_aliases = [
        "measured_shifts",
        "measured_shift_counts",
        "actual_shifts",
        "actual_shift_counts",
        "total_shifts",
        "shift_counts",
        "shifts",
        "measured",
        "trips",
        "trip_counts",
        "counts",
        "totals",
        "denominators",
        "实测班次",
        "实测班次数",
    ]
    on_time_aliases = [
        "on_time_shifts",
        "ontime_shifts",
        "on_time_shift_counts",
        "on_time_counts",
        "ontime_counts",
        "punctual_shifts",
        "punctual_counts",
        "qualified_shifts",
        "on_time",
        "ontime",
        "punctual",
        "numerators",
        "准点班次",
        "准点班次数",
    ]

    # Support flattened baseline_measured_shifts / current_on_time_shifts.
    prefixed_measured = [f"{prefix}_{alias}" for prefix in prefix_aliases for alias in measured_aliases]
    prefixed_on_time = [f"{prefix}_{alias}" for prefix in prefix_aliases for alias in on_time_aliases]

    if isinstance(period_obj, list) and len(period_obj) == 2:
        measured_found = True
        measured_value, on_time_value = period_obj[0], period_obj[1]
        on_time_found = True
    else:
        period_sources = [period_obj] if isinstance(period_obj, dict) else []
        # Nested period fields take priority. Then explicit flattened names
        # such as baseline_measured_shifts. Generic root aliases are last
        # because names like total_shifts may denote the schedule total.
        measured_found, measured_value, _ = find_value(period_sources, measured_aliases)
        if not measured_found:
            measured_found, measured_value, _ = find_value([payload], prefixed_measured)
        if not measured_found:
            measured_found, measured_value, _ = find_value([payload], measured_aliases)

        on_time_found, on_time_value, _ = find_value(period_sources, on_time_aliases)
        if not on_time_found:
            on_time_found, on_time_value, _ = find_value([payload], prefixed_on_time)
        if not on_time_found:
            on_time_found, on_time_value, _ = find_value([payload], on_time_aliases)

        if not measured_found:
            raise RequestDataError(
                f"缺少{period_label}的实测班次数矩阵。",
                [{"code": "missing_measured_matrix", "aliases": measured_aliases}],
            )
        if not on_time_found:
            # Allow omission only when every measured cell is zero/missing;
            # normalization below converts absent on-time counts to zero.
            on_time_value = None

    measured = parse_matrix(
        measured_value, m, n, f"{period_label}实测班次", allow_none=True
    )

    if on_time_value is None:
        on_time_raw = tuple(
            tuple(0 if value == 0 or value is None else None for value in row)
            for row in measured
        )
        if any(value is None for row in on_time_raw for value in row):
            raise RequestDataError(
                f"{period_label}存在有实测班次的格，但缺少准点班次数矩阵。",
                [{"code": "missing_on_time_matrix"}],
            )
        on_time = on_time_raw
    else:
        on_time = parse_matrix(
            on_time_value, m, n, f"{period_label}准点班次", allow_none=True
        )

    on_time_grid = [list(row) for row in on_time]

    # Semantic consistency. Missing measured values remain None so the guard
    # can list them; on-time values are normalized where safe.
    for i in range(m):
        for j in range(n):
            mv = measured[i][j]
            ov = on_time_grid[i][j]
            if mv is None:
                if ov is not None and ov > 0:
                    raise RequestDataError(
                        f"{period_label}第 {i + 1} 行第 {j + 1} 列没有实测班次，却有正准点班次。",
                        [{"row": i, "column": j, "measured": mv, "on_time": ov}],
                    )
                on_time_grid[i][j] = 0 if ov is None else ov
            elif mv == 0:
                if ov is not None and ov > 0:
                    raise RequestDataError(
                        f"{period_label}第 {i + 1} 行第 {j + 1} 列实测班次为 0，但准点班次大于 0。",
                        [{"row": i, "column": j, "measured": mv, "on_time": ov}],
                    )
                on_time_grid[i][j] = 0
            else:
                if ov is None:
                    raise RequestDataError(
                        f"{period_label}第 {i + 1} 行第 {j + 1} 列缺少准点班次数。",
                        [{"row": i, "column": j}],
                    )
                if ov > mv:
                    raise RequestDataError(
                        f"{period_label}第 {i + 1} 行第 {j + 1} 列准点班次多于实测班次。",
                        [{"row": i, "column": j, "measured": mv, "on_time": ov}],
                    )

    normalized_on_time = tuple(tuple(row) for row in on_time_grid)
    return PeriodMeasurements(measured=measured, on_time=normalized_on_time)


def _add_fixed_record(
    fixed: dict[tuple[int, int], int],
    i: int,
    j: int,
    value: Any,
    record: Any = None,
) -> None:
    count = parse_optional_int_cell(value, "已知格班次数", allow_none=False)
    assert count is not None
    key = (i, j)
    if key in fixed and fixed[key] != count:
        raise ScheduleConflictError(
            "同一个格存在不一致的已知班次数，无法拼成唯一符合记录的表。",
            [
                {
                    "code": "duplicate_fixed_cell_conflict",
                    "row": i,
                    "column": j,
                    "row_number": i + 1,
                    "column_number": j + 1,
                    "first_shifts": fixed[key],
                    "conflicting_shifts": count,
                    "record": record,
                }
            ],
        )
    fixed[key] = count


def parse_fixed_matrix(
    value: Any,
    m: int,
    n: int,
    fixed: dict[tuple[int, int], int],
) -> None:
    matrix = parse_matrix(value, m, n, "已知排班矩阵", allow_none=True)
    for i in range(m):
        for j in range(n):
            if matrix[i][j] is not None:
                _add_fixed_record(fixed, i, j, matrix[i][j])


def parse_fixed_cells(
    sources: list[dict[str, Any]],
    m: int,
    n: int,
    row_labels: tuple[str, ...] | None,
    col_labels: tuple[str, ...] | None,
    default_one_based: bool = False,
) -> dict[tuple[int, int], int]:
    fixed: dict[tuple[int, int], int] = {}

    list_aliases = [
        "fixed_cells",
        "known_cells",
        "given_cells",
        "preset_cells",
        "specified_cells",
        "sparse_cells",
        "cell_counts",
        "cells",
        "known_counts",
        "fixed_counts",
        "partial_counts",
        "known_shift_counts",
        "fixed_shift_counts",
        "schedule_records",
        "records",
        "fixed",
        "known",
        "固定格",
        "已知格",
    ]
    matrix_aliases = [
        "fixed_matrix",
        "known_matrix",
        "given_matrix",
        "partial_schedule_matrix",
        "schedule_matrix",
        "scheduled_matrix",
        "scheduled_shifts_matrix",
        "固定排班矩阵",
    ]

    found, value, _ = find_value(sources, list_aliases)
    if found and value is not None and value is not False:
        records: Any = value
        if isinstance(records, dict):
            expanded = []
            if records and all(isinstance(part, dict) for part in records.values()):
                # Nested mapping: {"0": {"1": 4, "2": 3}, "干线": {"早高峰": 2}}
                for row_key, columns in records.items():
                    if not isinstance(columns, dict):
                        continue
                    for col_key, count in columns.items():
                        expanded.append({"row": row_key, "column": col_key, "shifts": count})
            else:
                # Flat mapping of "0,1": count, "0|1": count, or a record key.
                for key, count in records.items():
                    if isinstance(key, str) and "," in key:
                        rs, cs = [part.strip() for part in key.split(",", 1)]
                        record = {"row": rs, "column": cs, "shifts": count}
                    elif isinstance(key, str) and "|" in key:
                        rs, cs = [part.strip() for part in key.split("|", 1)]
                        record = {"row": rs, "column": cs, "shifts": count}
                    elif isinstance(count, dict):
                        record = dict(count)
                        record.setdefault("row", key)
                        expanded.append(record)
                        continue
                    else:
                        record = {"key": key, "shifts": count}
                    expanded.append(record)
            records = expanded
        if not isinstance(records, list):
            raise RequestDataError("fixed_cells 必须是记录数组或坐标到班次数的对象。")

        for record in records:
            if isinstance(record, (list, tuple)) and len(record) in (2, 3):
                if len(record) == 3:
                    ri, cj, count = record
                else:
                    raise RequestDataError("固定格数组记录应为 [row, column, shifts]。", [{"record": record}])
                i = as_index(ri, m, "行")
                j = as_index(cj, n, "列")
                _add_fixed_record(fixed, i, j, count, record)
                continue

            if not isinstance(record, dict):
                raise RequestDataError("固定格记录必须是对象或 [row, column, shifts]。", [{"record": record}])

            i = resolve_coordinate(record, "row", m, row_labels, default_one_based)
            j = resolve_coordinate(record, "column", n, col_labels, default_one_based)
            found_count, count, _ = find_value(
                [record],
                [
                    "shifts",
                    "scheduled_shifts",
                    "shift_count",
                    "count",
                    "value",
                    "trips",
                    "number",
                    "banci",
                    "班次数",
                ],
            )
            if not found_count:
                raise RequestDataError("固定格记录缺少 shifts/count/value。", [{"record": record}])
            _add_fixed_record(fixed, i, j, count, record)

    for alias in matrix_aliases:
        found, value, _ = find_value(sources, [alias])
        if found:
            if not isinstance(value, list):
                raise RequestDataError(f"{alias} 必须是二维数组，null 表示未知格。")
            parse_fixed_matrix(value, m, n, fixed)

    # A `schedule` value that is itself a matrix is a partial/full schedule.
    found, schedule_value, _ = find_value(sources, ["schedule", "standard_schedule", "排班表"])
    if found and isinstance(schedule_value, list):
        parse_fixed_matrix(schedule_value, m, n, fixed)
    elif found and isinstance(schedule_value, dict):
        found_matrix, matrix_value = _lookup_in_dict(
            schedule_value, ["matrix", "cells_matrix", "scheduled_shifts", "table", "排班表"]
        )
        if found_matrix and isinstance(matrix_value, list):
            parse_fixed_matrix(matrix_value, m, n, fixed)

    # A `scheduled_shifts` 2D array may also appear directly at root/period-like
    # schedule source. Only use it if it looks like a matrix.
    found, scheduled_value, _ = find_value(sources, ["scheduled_shifts", "排班数", "班次数"])
    if found and isinstance(scheduled_value, list) and (
        not scheduled_value or isinstance(scheduled_value[0], list)
    ):
        parse_fixed_matrix(scheduled_value, m, n, fixed)

    return fixed


def parse_assessment_request(payload: Any) -> AssessmentRequest:
    if not isinstance(payload, dict):
        raise RequestDataError("请求体必须是 JSON 对象。")

    schedule_obj = payload.get("schedule")
    sources: list[dict[str, Any]] = []
    if isinstance(schedule_obj, dict):
        sources.append(schedule_obj)
    sources.append(payload)

    row_totals_value = require_value(
        sources,
        [
            "row_totals",
            "row_total",
            "row_sums",
            "rows_total",
            "row_counts",
            "row_records",
            "route_totals",
            "line_totals",
            "rows",
            "行合计",
            "rowtotals",
        ],
        "row_totals（行合计）",
    )
    col_totals_value = require_value(
        sources,
        [
            "column_totals",
            "column_total",
            "col_totals",
            "col_total",
            "column_sums",
            "col_sums",
            "columns_total",
            "column_records",
            "col_records",
            "time_period_totals",
            "columns",
            "cols",
            "列合计",
            "coltotals",
        ],
        "column_totals（列合计）",
    )

    row_totals, dict_row_labels = parse_total_vector(row_totals_value, "行")
    col_totals, dict_col_labels = parse_total_vector(col_totals_value, "列")
    m = len(row_totals)
    n = len(col_totals)

    found, row_label_value, _ = find_value(
        sources,
        ["row_labels", "row_names", "rows", "route_categories", "route_category_names", "lines", "line_names", "行标签", "线路类别"],
    )
    row_labels = parse_labels(row_label_value, m, "行") if found else dict_row_labels

    found, col_label_value, _ = find_value(
        sources,
        ["column_labels", "column_names", "col_labels", "columns", "time_periods", "period_labels", "slots", "列标签", "时段"],
    )
    column_labels = parse_labels(col_label_value, n, "列") if found else dict_col_labels

    total = sum(row_totals)
    if not MIN_TOTAL <= total <= MAX_TOTAL:
        raise RequestDataError(
            f"标准排班总数（行合计之和）必须在 {MIN_TOTAL} 到 {MAX_TOTAL} 班之间。",
            [{"code": "total_out_of_range", "total": total, "min": MIN_TOTAL, "max": MAX_TOTAL}],
        )

    declared_total = None
    found, declared_value, _ = find_value(
        sources,
        ["total_shifts", "scheduled_total", "schedule_total", "standard_total", "total", "排班总数"],
    )
    if found:
        declared_total = parse_optional_int_cell(declared_value, "排班总数", allow_none=False)

    # Default JSON coordinates are zero-based because they address arrays.
    # Clients using human-friendly 1-based "row"/"column" can set
    # "index_base": 1 or "one_based": true. Explicit row_number is always 1-based.
    default_one_based = False
    found, index_base_value, _ = find_value(
        sources, ["index_base", "coordinate_base", "indexing", "coordinate_system"]
    )
    if found:
        if isinstance(index_base_value, str):
            digits = re.search(r"[01]", index_base_value)
            index_base = int(digits.group(0)) if digits else None
            if "1-based" in index_base_value.lower() or "一基" in index_base_value:
                index_base = 1
            elif "0-based" in index_base_value.lower() or "零基" in index_base_value:
                index_base = 0
        elif isinstance(index_base_value, (int, float)) and not isinstance(index_base_value, bool):
            index_base = int(index_base_value)
        else:
            index_base = None
        if index_base not in (0, 1):
            raise RequestDataError(
                "index_base/coordinate_base 只支持 0 或 1。",
                [{"code": "invalid_index_base", "value": index_base_value}],
            )
        default_one_based = index_base == 1

    found, one_based_value, _ = find_value(sources, ["one_based", "is_one_based", "use_one_based_numbers"])
    if found:
        if not isinstance(one_based_value, bool):
            raise RequestDataError(
                "one_based 必须是布尔值。",
                [{"code": "invalid_one_based", "value": one_based_value}],
            )
        default_one_based = one_based_value

    fixed_cells = parse_fixed_cells(
        sources, m, n, row_labels, column_labels, default_one_based=default_one_based
    )

    baseline = parse_period(
        payload,
        ["baseline", "base", "base_period", "previous", "previous_period", "period_baseline", "jizhun", "基期"],
        ["baseline", "base", "previous", "prev"],
        m,
        n,
        "基期",
    )
    current = parse_period(
        payload,
        ["current", "current_period", "report", "report_period", "actual_period", "benqi", "本期"],
        ["current", "report", "actual"],
        m,
        n,
        "本期",
    )

    if row_labels is None:
        row_labels = tuple(f"第{i + 1}行" for i in range(m))
    if column_labels is None:
        column_labels = tuple(f"第{j + 1}列" for j in range(n))

    return AssessmentRequest(
        row_totals=row_totals,
        column_totals=col_totals,
        fixed_cells=fixed_cells,
        baseline=baseline,
        current=current,
        row_labels=row_labels,
        column_labels=column_labels,
        declared_total=declared_total,
    )
