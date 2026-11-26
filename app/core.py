from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from fractions import Fraction

MAX_DIM = 6
MIN_TOTAL = 1
MAX_TOTAL = 100_000

# 自测与核对用的求解计数：feasibility_solves 统计排班可行性最大流次数，
# min_cost_solves 统计上下界费用流次数（一次取界含最小/最大两次）。
SOLVER_STATS = {"feasibility_solves": 0, "min_cost_solves": 0}


def reset_solver_stats() -> None:
    SOLVER_STATS["feasibility_solves"] = 0
    SOLVER_STATS["min_cost_solves"] = 0


def get_solver_stats() -> dict[str, int]:
    return dict(SOLVER_STATS)


class AssessmentError(Exception):
    """Base class for domain/request errors carrying structured details."""

    code = "assessment_error"
    http_status = 400

    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []


class RequestDataError(AssessmentError):
    code = "invalid_request"
    http_status = 422


class ScheduleConflictError(AssessmentError):
    code = "schedule_conflict"
    http_status = 409


class MissingMeasurementError(AssessmentError):
    code = "missing_measurements"
    http_status = 422

    def __init__(self, message: str, blocked_cells: list[dict]):
        super().__init__(message, blocked_cells)
        self.blocked_cells = blocked_cells


@dataclass(frozen=True)
class PeriodMeasurements:
    measured: tuple[tuple[int | None, ...], ...]
    on_time: tuple[tuple[int | None, ...], ...]


@dataclass(frozen=True)
class AssessmentRequest:
    row_totals: tuple[int, ...]
    column_totals: tuple[int, ...]
    fixed_cells: dict[tuple[int, int], int]
    baseline: PeriodMeasurements
    current: PeriodMeasurements
    row_labels: tuple[str, ...] | None = None
    column_labels: tuple[str, ...] | None = None
    declared_total: int | None = None


@dataclass
class BoundResult:
    kind: str
    difference: Fraction
    schedule: list[list[int]]
    baseline_rate: Fraction
    current_rate: Fraction

    def as_dict(self) -> dict:
        diff = self.difference
        diff_obj = {
            "numerator": diff.numerator,
            "denominator": diff.denominator,
            "fraction": format_fraction(diff),
        }
        return {
            "kind": self.kind,
            "difference": diff_obj,
            # Convenience aliases for clients that expect a scalar field.
            "value": format_fraction(diff),
            "fraction": format_fraction(diff),
            "difference_fraction": format_fraction(diff),
            "numerator": diff.numerator,
            "denominator": diff.denominator,
            "baseline_weighted_on_time_rate": fraction_dict(self.baseline_rate),
            "current_weighted_on_time_rate": fraction_dict(self.current_rate),
            "schedule": self.schedule,
            "schedule_matrix": self.schedule,
            "table": self.schedule,
        }


@dataclass
class AssessmentResult:
    total: int
    minimum: BoundResult
    maximum: BoundResult
    possible_positive: list[list[bool]]

    def as_dict(self) -> dict:
        min_dict = self.minimum.as_dict()
        max_dict = self.maximum.as_dict()
        possible_cells = [
            {
                "row": i,
                "column": j,
                "col": j,
                "i": i,
                "j": j,
                "row_index": i,
                "column_index": j,
                "row_number": i + 1,
                "column_number": j + 1,
            }
            for i, row in enumerate(self.possible_positive)
            for j, possible in enumerate(row)
            if possible
        ]
        return {
            "status": "ok",
            "total_scheduled_shifts": self.total,
            "minimum": min_dict,
            "maximum": max_dict,
            # Short aliases.
            "min": min_dict,
            "max": max_dict,
            # Flat aliases for lightweight clients and test assertions.
            "minimum_difference": min_dict["difference"],
            "maximum_difference": max_dict["difference"],
            "minimum_value": min_dict["fraction"],
            "maximum_value": max_dict["fraction"],
            "min_difference": min_dict["difference"]["fraction"],
            "max_difference": max_dict["difference"]["fraction"],
            "min_value": min_dict["fraction"],
            "max_value": max_dict["fraction"],
            "minimum_schedule": min_dict["schedule"],
            "maximum_schedule": max_dict["schedule"],
            "minimum_table": min_dict["schedule"],
            "maximum_table": max_dict["schedule"],
            "min_schedule": min_dict["schedule"],
            "max_schedule": max_dict["schedule"],
            "min_table": min_dict["schedule"],
            "max_table": max_dict["schedule"],
            "possible_positive_cells": possible_cells,
            "blocked_cells": [],
        }


def format_fraction(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def fraction_dict(value: Fraction) -> dict:
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "fraction": format_fraction(value),
    }


# ---------------------------------------------------------------------------
# Flow feasibility
#
# 月末批量试算时，调用方会反复尝试给未知格补 1 班来寻找需要补报实测的格。
# 旧实现对每个未知格单独跑一次“强制至少 1 班”的最大流（最多 m*n 次）。
# 现在整次请求只构造并求解一次残量运输网络：可行性由该最大流判定，
# “哪些格能在至少一张可行表中排到班次”直接从同一张残量图的可达性读出，
# 不再逐格发起排班可行性求解。
# ---------------------------------------------------------------------------


class _Dinic:
    def __init__(self, n: int):
        self.n = n
        self.graph: list[list[list[int]]] = [[] for _ in range(n)]

    def add_edge(self, source: int, target: int, capacity: int):
        forward = [target, len(self.graph[target]), capacity]
        backward = [source, len(self.graph[source]), 0]
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    def max_flow(self, source: int, sink: int) -> int:
        flow = 0
        while True:
            level = [-1] * self.n
            level[source] = 0
            queue = deque([source])
            while queue:
                v = queue.popleft()
                for to, _rev, cap in self.graph[v]:
                    if cap > 0 and level[to] < 0:
                        level[to] = level[v] + 1
                        queue.append(to)
            if level[sink] < 0:
                return flow

            next_edge = [0] * self.n

            def dfs(v: int, pushed: int) -> int:
                if v == sink:
                    return pushed
                while next_edge[v] < len(self.graph[v]):
                    edge = self.graph[v][next_edge[v]]
                    to, rev, cap = edge
                    if cap > 0 and level[to] == level[v] + 1:
                        sent = dfs(to, min(pushed, cap))
                        if sent:
                            edge[2] -= sent
                            self.graph[to][rev][2] += sent
                            return sent
                    next_edge[v] += 1
                return 0

            while True:
                pushed = dfs(source, 10**18)
                if pushed == 0:
                    break
                flow += pushed


@dataclass
class FeasibilityAnalysis:
    """一次最大流求解的全部复用产物。"""

    feasible: bool
    row_residual: list[int]
    col_residual: list[int]
    unknown_cells: list[tuple[int, int]]
    possible_positive: list[list[bool]]


def _residuals(request: AssessmentRequest) -> tuple[list[int], list[int]]:
    row_residual = list(request.row_totals)
    col_residual = list(request.column_totals)
    for (i, j), value in request.fixed_cells.items():
        row_residual[i] -= value
        col_residual[j] -= value
    return row_residual, col_residual


def analyze_schedule_feasibility(request: AssessmentRequest) -> FeasibilityAnalysis:
    """至多运行一次排班可行性最大流，并同时求出“可能为正”的格掩码。

    判定依据（整数运输网络）：
    - 网络从源点到行、再经未知格边到列、最后到汇点推送全部残量班次；
      最大流达到残量总和当且仅当存在至少一张可行排班表。
    - 对一条未知格边 (row i -> col j)，它能在某张可行表中带正流量，当且仅当
      该边已有正流量，或者在同一最大流的残量图上 col j 可以到达 row i
      （沿残量边形成增广圈，沿圈调整即可把流量引入该边且保持整数性）。
    """
    m = len(request.row_totals)
    n = len(request.column_totals)
    row_residual, col_residual = _residuals(request)

    unknown_cells = [
        (i, j)
        for i in range(m)
        for j in range(n)
        if (i, j) not in request.fixed_cells
    ]
    possible = [[False] * n for _ in range(m)]
    for (i, j), value in request.fixed_cells.items():
        if value > 0:
            possible[i][j] = True

    if any(x < 0 for x in row_residual) or any(x < 0 for x in col_residual):
        return FeasibilityAnalysis(False, row_residual, col_residual, unknown_cells, possible)

    residual_total = sum(row_residual)
    if residual_total != sum(col_residual):
        return FeasibilityAnalysis(False, row_residual, col_residual, unknown_cells, possible)

    if residual_total == 0:
        # 无残量可排：唯一可行表由固定格完全决定，未知格恒为 0。
        # 这种情形不需要任何最大流求解。
        return FeasibilityAnalysis(True, row_residual, col_residual, unknown_cells, possible)

    SOLVER_STATS["feasibility_solves"] += 1
    source = 0
    row_start = 1
    col_start = row_start + m
    sink = col_start + n
    dinic = _Dinic(sink + 1)

    for i, amount in enumerate(row_residual):
        if amount:
            dinic.add_edge(source, row_start + i, amount)
    for j, amount in enumerate(col_residual):
        if amount:
            dinic.add_edge(col_start + j, sink, amount)

    cell_edges: dict[tuple[int, int], list[int]] = {}
    for i, j in unknown_cells:
        edge = dinic.add_edge(row_start + i, col_start + j, residual_total)
        cell_edges[(i, j)] = edge

    flow = dinic.max_flow(source, sink)
    if flow != residual_total:
        return FeasibilityAnalysis(False, row_residual, col_residual, unknown_cells, possible)

    # 只在列侧残量网络上做 BFS：列节点只能沿格边/格边反向在行列节点间移动，
    # 永远到不了源点，因此源/汇边不会干扰可达关系。
    reachable_from_col: list[frozenset[int] | None] = [None] * n

    def reachable_set(start_col: int) -> frozenset[int]:
        cached = reachable_from_col[start_col]
        if cached is not None:
            return cached
        seen = {col_start + start_col}
        queue = deque([col_start + start_col])
        while queue:
            v = queue.popleft()
            for to, _rev, cap in dinic.graph[v]:
                if cap > 0 and to not in seen:
                    seen.add(to)
                    queue.append(to)
        result = frozenset(seen)
        reachable_from_col[start_col] = result
        return result

    for (i, j), edge in cell_edges.items():
        used = residual_total - edge[2]
        if used > 0 or (row_start + i) in reachable_set(j):
            possible[i][j] = True

    return FeasibilityAnalysis(True, row_residual, col_residual, unknown_cells, possible)


# ---------------------------------------------------------------------------
# Exact minimum-cost flow
# ---------------------------------------------------------------------------


@dataclass
class _CostEdge:
    to: int
    rev: int
    cap: int
    cost: Fraction
    initial_cap: int


class _MinCostFlow:
    def __init__(self, n: int):
        self.n = n
        self.graph: list[list[_CostEdge]] = [[] for _ in range(n)]

    def add_edge(self, source: int, target: int, capacity: int, cost: Fraction) -> _CostEdge:
        forward = _CostEdge(
            to=target,
            rev=len(self.graph[target]),
            cap=capacity,
            cost=cost,
            initial_cap=capacity,
        )
        backward = _CostEdge(
            to=source,
            rev=len(self.graph[source]),
            cap=0,
            cost=-cost,
            initial_cap=0,
        )
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    def min_cost_flow(self, source: int, sink: int, required: int) -> tuple[int, Fraction]:
        flow = 0
        total_cost = Fraction(0)

        while flow < required:
            dist: list[Fraction | None] = [None] * self.n
            previous_vertex = [-1] * self.n
            previous_edge = [-1] * self.n
            dist[source] = Fraction(0)

            # Bellman-Ford is more than fast enough: at most 8 row/column
            # categories means only a handful of vertices and edges. It also
            # handles the initially negative cell costs exactly.
            for _ in range(max(1, self.n - 1)):
                changed = False
                for v in range(self.n):
                    if dist[v] is None:
                        continue
                    dv = dist[v]
                    assert dv is not None
                    for edge_index, edge in enumerate(self.graph[v]):
                        if edge.cap <= 0:
                            continue
                        candidate = dv + edge.cost
                        if dist[edge.to] is None or candidate < dist[edge.to]:
                            dist[edge.to] = candidate
                            previous_vertex[edge.to] = v
                            previous_edge[edge.to] = edge_index
                            changed = True
                if not changed:
                    break

            if dist[sink] is None:
                break

            added = required - flow
            v = sink
            while v != source:
                pv = previous_vertex[v]
                pe = previous_edge[v]
                if pv < 0 or pe < 0:
                    break
                added = min(added, self.graph[pv][pe].cap)
                v = pv

            if added <= 0:
                break

            assert dist[sink] is not None
            total_cost += added * dist[sink]
            flow += added

            v = sink
            while v != source:
                pv = previous_vertex[v]
                pe = previous_edge[v]
                edge = self.graph[pv][pe]
                edge.cap -= added
                self.graph[v][edge.rev].cap += added
                v = pv

        return flow, total_cost


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def _arithmetic_conflicts(request: AssessmentRequest) -> list[dict]:
    """纯算术类冲突检查，不触发任何排班可行性求解。"""
    conflicts: list[dict] = []

    row_sum = sum(request.row_totals)
    col_sum = sum(request.column_totals)

    if request.declared_total is not None and request.declared_total != row_sum:
        conflicts.append(
            {
                "code": "declared_total_does_not_match_rows",
                "message": "申报的排班总数与行合计之和不一致。",
                "declared_total": request.declared_total,
                "row_total_sum": row_sum,
            }
        )
    if request.declared_total is not None and request.declared_total != col_sum:
        conflicts.append(
            {
                "code": "declared_total_does_not_match_columns",
                "message": "申报的排班总数与列合计之和不一致。",
                "declared_total": request.declared_total,
                "column_total_sum": col_sum,
            }
        )
    if row_sum != col_sum:
        conflicts.append(
            {
                "code": "row_column_sum_mismatch",
                "message": "行合计之和与列合计之和不一致，无法拼成排班表。",
                "row_total_sum": row_sum,
                "column_total_sum": col_sum,
            }
        )

    for i, row_total in enumerate(request.row_totals):
        fixed_sum = sum(v for (r, _c), v in request.fixed_cells.items() if r == i)
        if fixed_sum > row_total:
            conflicts.append(
                {
                    "code": "fixed_row_exceeds_row_total",
                    "message": f"第 {i + 1} 行已知班次数超过该行合计。",
                    "row": i,
                    "row_number": i + 1,
                    "row_total": row_total,
                    "fixed_sum": fixed_sum,
                    "excess": fixed_sum - row_total,
                }
            )

    for j, col_total in enumerate(request.column_totals):
        fixed_sum = sum(v for (_r, c), v in request.fixed_cells.items() if c == j)
        if fixed_sum > col_total:
            conflicts.append(
                {
                    "code": "fixed_column_exceeds_column_total",
                    "message": f"第 {j + 1} 列已知班次数超过该列合计。",
                    "column": j,
                    "column_number": j + 1,
                    "column_total": col_total,
                    "fixed_sum": fixed_sum,
                    "excess": fixed_sum - col_total,
                }
            )

    return conflicts


def _residual_conflict_detail(analysis: FeasibilityAnalysis) -> dict:
    return {
        "code": "residual_transportation_infeasible",
        "message": "扣除已知格后，剩余行列合计无法由未知格的非负整数班次数拼成。",
        "row_residual_totals": analysis.row_residual,
        "column_residual_totals": analysis.col_residual,
        "unknown_cells": [
            {
                "row": i,
                "column": j,
                "col": j,
                "i": i,
                "j": j,
                "row_index": i,
                "column_index": j,
                "row_number": i + 1,
                "column_number": j + 1,
            }
            for i, j in analysis.unknown_cells
        ],
    }


def validate_schedule_shape(request: AssessmentRequest) -> list[dict]:
    """Return structured schedule conflicts; an empty list means feasible.

    整个检查只允许触发一次排班可行性求解（见
    :func:`analyze_schedule_feasibility`）：算术类冲突直接判定，结构可行性
    与“可能为正”的格集合共享同一次最大流的结果。
    """
    conflicts = _arithmetic_conflicts(request)

    # If arithmetic already fails, no residual flow test is needed (and the
    # request performs zero feasibility solves on this rejection path).
    if conflicts:
        return conflicts

    analysis = analyze_schedule_feasibility(request)
    if not analysis.feasible:
        conflicts.append(_residual_conflict_detail(analysis))

    return conflicts


def find_possible_positive_cells(request: AssessmentRequest) -> list[list[bool]]:
    """返回每个格能否在至少一张可行排班表中排到班次（单次可行性求解）。"""
    analysis = analyze_schedule_feasibility(request)
    if not analysis.feasible:
        m = len(request.row_totals)
        n = len(request.column_totals)
        return [[False] * n for _ in range(m)]
    return analysis.possible_positive


def _cell_rate_difference(request: AssessmentRequest, i: int, j: int) -> Fraction:
    bm = request.baseline.measured[i][j]
    bo = request.baseline.on_time[i][j]
    cm = request.current.measured[i][j]
    co = request.current.on_time[i][j]
    if not bm or not cm or bo is None or co is None:
        # Such a coefficient is never used after the measurement guard.
        return Fraction(0)
    return Fraction(co, cm) - Fraction(bo, bm)


def _extreme_schedule(
    request: AssessmentRequest,
    coefficients: list[list[Fraction]],
    minimize: bool,
    possible: list[list[bool]] | None = None,
) -> tuple[list[list[int]], Fraction]:
    m = len(request.row_totals)
    n = len(request.column_totals)
    total = sum(request.row_totals)

    row_residual = list(request.row_totals)
    col_residual = list(request.column_totals)
    for (i, j), value in request.fixed_cells.items():
        row_residual[i] -= value
        col_residual[j] -= value

    # 实测足够出数后，把已确定排不到班的未知格直接从上下界费用流中剔除。
    # 这些格在所有可行表中恒为 0，删除对应边不改变极值，只缩小求解规模。
    if possible is None:
        unknown_cells = [
            (i, j)
            for i in range(m)
            for j in range(n)
            if (i, j) not in request.fixed_cells
        ]
    else:
        unknown_cells = [
            (i, j)
            for i in range(m)
            for j in range(n)
            if (i, j) not in request.fixed_cells and possible[i][j]
        ]
    residual_total = sum(row_residual)

    source = 0
    row_start = 1
    col_start = row_start + m
    sink = col_start + n
    mcf = _MinCostFlow(sink + 1)

    for i, amount in enumerate(row_residual):
        mcf.add_edge(source, row_start + i, amount, Fraction(0))
    for j, amount in enumerate(col_residual):
        mcf.add_edge(col_start + j, sink, amount, Fraction(0))

    edge_refs: dict[tuple[int, int], _CostEdge] = {}
    for i, j in unknown_cells:
        cost = coefficients[i][j] if minimize else -coefficients[i][j]
        edge = mcf.add_edge(row_start + i, col_start + j, residual_total, cost)
        edge_refs[(i, j)] = edge

    sent, flow_cost = mcf.min_cost_flow(source, sink, residual_total)
    SOLVER_STATS["min_cost_solves"] += 1
    if sent != residual_total:
        # validate_schedule_shape should have caught this before optimization.
        raise ScheduleConflictError(
            "排班残量流优化失败，未找到完整可行表。",
            [{"code": "optimization_flow_incomplete", "sent": sent, "required": residual_total}],
        )

    table = [[0] * n for _ in range(m)]
    for (i, j), value in request.fixed_cells.items():
        table[i][j] = value

    for cell, edge in edge_refs.items():
        i, j = cell
        used = edge.initial_cap - edge.cap
        # Defensive invariant: reverse capacity should equal used flow.
        if used < 0:
            raise AssessmentError("内部流计算出现负排班数。")
        table[i][j] += used

    fixed_objective = Fraction(0)
    for (i, j), value in request.fixed_cells.items():
        if value:
            fixed_objective += value * coefficients[i][j]

    if minimize:
        objective_sum = fixed_objective + flow_cost
    else:
        # flow_cost is min of -sum(x * d), so negate it to recover max sum.
        objective_sum = fixed_objective - flow_cost

    return table, objective_sum


def _rates_for_table(
    request: AssessmentRequest, table: list[list[int]]
) -> tuple[Fraction, Fraction]:
    total = sum(sum(row) for row in table)
    if total <= 0:
        raise AssessmentError("排班总数必须为正数。")

    baseline = Fraction(0)
    current = Fraction(0)
    for i, row in enumerate(table):
        for j, shifts in enumerate(row):
            if shifts == 0:
                continue
            bm = request.baseline.measured[i][j]
            bo = request.baseline.on_time[i][j]
            cm = request.current.measured[i][j]
            co = request.current.on_time[i][j]
            if not bm or bo is None or not cm or co is None:
                # The caller's guard makes this unreachable for a witness.
                raise MissingMeasurementError(
                    "达到极值的排班使用了无实测班次的格。",
                    [{"row": i, "column": j}],
                )
            baseline += shifts * Fraction(bo, bm)
            current += shifts * Fraction(co, cm)
    return baseline / total, current / total


def assess(request: AssessmentRequest) -> AssessmentResult:
    total = sum(request.row_totals)
    if not (MIN_TOTAL <= total <= MAX_TOTAL):
        # Parsing normally checks this, but keep the domain boundary explicit.
        raise RequestDataError(
            f"标准排班总数必须在 {MIN_TOTAL} 到 {MAX_TOTAL} 之间。",
            [{"code": "total_out_of_range", "total": total}],
        )

    arithmetic = _arithmetic_conflicts(request)
    if arithmetic:
        # 算术已失败时无需也无法构造残量网络，本路径不产生可行性求解。
        raise ScheduleConflictError(
            "排班记录不一致，无法构造任何符合条件的排班表。", arithmetic
        )

    # 整次请求唯一一次排班可行性求解：可行性判定与“可能为正”格掩码同源复用。
    analysis = analyze_schedule_feasibility(request)
    if not analysis.feasible:
        raise ScheduleConflictError(
            "排班记录不一致，无法构造任何符合条件的排班表。",
            [_residual_conflict_detail(analysis)],
        )
    possible = analysis.possible_positive

    blocked: list[dict] = []
    for i in range(len(request.row_totals)):
        for j in range(len(request.column_totals)):
            if not possible[i][j]:
                continue
            bm = request.baseline.measured[i][j]
            cm = request.current.measured[i][j]
            baseline_missing = bm is None or bm == 0
            current_missing = cm is None or cm == 0
            if baseline_missing or current_missing:
                missing_in = []
                if baseline_missing:
                    missing_in.append("baseline")
                if current_missing:
                    missing_in.append("current")
                item = {
                    "row": i,
                    "column": j,
                    "col": j,
                    "i": i,
                    "j": j,
                    "row_index": i,
                    "column_index": j,
                    "row_number": i + 1,
                    "column_number": j + 1,
                    "missing_in": missing_in,
                    "baseline_missing": baseline_missing,
                    "current_missing": current_missing,
                    "baseline_measured_shifts": bm,
                    "current_measured_shifts": cm,
                }
                if request.row_labels is not None:
                    item["row_label"] = request.row_labels[i]
                if request.column_labels is not None:
                    item["column_label"] = request.column_labels[j]
                blocked.append(item)

    if blocked:
        # 缺实测退回发生在率差取界（费用流）之前：本路径只做了 1 次可行性
        # 求解，0 次取界求解。
        raise MissingMeasurementError(
            "存在可能排有班次但某一期没有实测班次的格；为避免无依据加权，本次不出数。",
            blocked,
        )

    m = len(request.row_totals)
    n = len(request.column_totals)
    coefficients = [
        [_cell_rate_difference(request, i, j) for j in range(n)]
        for i in range(m)
    ]

    # 实测足够出数：把确定排不到班的未知格从上下界费用流中剔除，
    # 极值与见证表保持精确（这些格在所有可行表中恒为 0）。
    min_table, min_sum = _extreme_schedule(request, coefficients, True, possible)
    max_table, max_sum = _extreme_schedule(request, coefficients, False, possible)

    min_diff = min_sum / total
    max_diff = max_sum / total

    min_base_rate, min_current_rate = _rates_for_table(request, min_table)
    max_base_rate, max_current_rate = _rates_for_table(request, max_table)

    # Exact witness verification.
    if min_current_rate - min_base_rate != min_diff:
        raise AssessmentError("最小值见证表核算不一致。")
    if max_current_rate - max_base_rate != max_diff:
        raise AssessmentError("最大值见证表核算不一致。")
    if min_diff > max_diff:
        raise AssessmentError("内部极值顺序错误。")

    return AssessmentResult(
        total=total,
        minimum=BoundResult(
            kind="minimum",
            difference=min_diff,
            schedule=min_table,
            baseline_rate=min_base_rate,
            current_rate=min_current_rate,
        ),
        maximum=BoundResult(
            kind="maximum",
            difference=max_diff,
            schedule=max_table,
            baseline_rate=max_base_rate,
            current_rate=max_current_rate,
        ),
        possible_positive=possible,
    )
