from __future__ import annotations

import itertools
import random
import time
import unittest
from fractions import Fraction

from app.core import (
    AssessmentRequest,
    MissingMeasurementError,
    PeriodMeasurements,
    ScheduleConflictError,
    assess,
    find_possible_positive_cells,
    get_solver_stats,
    reset_solver_stats,
)
from app.parsing import parse_assessment_request


def compositions(total: int, parts: int):
    if parts == 1:
        yield (total,)
        return
    for first in range(total + 1):
        for rest in compositions(total - first, parts - 1):
            yield (first,) + rest


def enumerate_tables(row_totals, col_totals, fixed):
    m = len(row_totals)
    n = len(col_totals)
    output = []
    for rows in itertools.product(*[list(compositions(r, n)) for r in row_totals]):
        table = [list(row) for row in rows]
        if [sum(table[i][j] for i in range(m)) for j in range(n)] != list(col_totals):
            continue
        if any(table[i][j] != value for (i, j), value in fixed.items()):
            continue
        output.append(table)
    return output


def make_period(rng, m, n, max_measured=9):
    measured = []
    on_time = []
    for _ in range(m):
        m_row = []
        o_row = []
        for _ in range(n):
            total = rng.randint(1, max_measured)
            on = rng.randint(0, total)
            m_row.append(total)
            o_row.append(on)
        measured.append(m_row)
        on_time.append(o_row)
    return PeriodMeasurements(
        measured=tuple(tuple(row) for row in measured),
        on_time=tuple(tuple(row) for row in on_time),
    )


def request_from_parts(row_totals, col_totals, fixed, baseline, current):
    return AssessmentRequest(
        row_totals=tuple(row_totals),
        column_totals=tuple(col_totals),
        fixed_cells=dict(fixed),
        baseline=baseline,
        current=current,
    )


class BruteForceOptimizationTests(unittest.TestCase):
    def test_random_small_feasible_cases_match_brute_force(self):
        rng = random.Random(20261126)
        for case_index in range(120):
            m = rng.randint(1, 3)
            n = rng.randint(1, 3)
            total = rng.randint(1, 7)

            flat = [rng.randint(0, 3) for _ in range(m * n)]
            deficit = total - sum(flat)
            flat[rng.randrange(len(flat))] += max(0, deficit)
            # If over total, scale-ish by regenerating through random cuts is
            # unnecessary for a test: clamp then add remaining.
            table = [flat[i * n : (i + 1) * n] for i in range(m)]
            row_totals = [sum(row) for row in table]
            col_totals = [sum(table[i][j] for i in range(m)) for j in range(n)]
            total = sum(row_totals)
            if total == 0:
                row_totals[0] += 1
                table[0][0] += 1
                col_totals[0] += 1
                total = 1

            fixed = {}
            for i in range(m):
                for j in range(n):
                    if rng.random() < 0.28:
                        fixed[(i, j)] = table[i][j]

            baseline = make_period(rng, m, n)
            current = make_period(rng, m, n)
            req = request_from_parts(row_totals, col_totals, fixed, baseline, current)

            feasible_tables = enumerate_tables(row_totals, col_totals, fixed)
            self.assertTrue(feasible_tables, f"case {case_index}: generated table missing")

            coefficients = [
                [
                    Fraction(current.on_time[i][j], current.measured[i][j])
                    - Fraction(baseline.on_time[i][j], baseline.measured[i][j])
                    for j in range(n)
                ]
                for i in range(m)
            ]
            brute_values = [
                sum(
                    (Fraction(table[i][j]) * coefficients[i][j] for i in range(m) for j in range(n)),
                    Fraction(0),
                )
                / total
                for table in feasible_tables
            ]
            brute_min = min(brute_values)
            brute_max = max(brute_values)

            possible = find_possible_positive_cells(req)
            brute_possible = [
                [any(t[i][j] > 0 for t in feasible_tables) for j in range(n)]
                for i in range(m)
            ]
            self.assertEqual(possible, brute_possible, f"possible mask mismatch case {case_index}")

            result = assess(req)
            self.assertEqual(result.minimum.difference, brute_min, f"min mismatch case {case_index}")
            self.assertEqual(result.maximum.difference, brute_max, f"max mismatch case {case_index}")

            for bound, expected in [(result.minimum, brute_min), (result.maximum, brute_max)]:
                t = bound.schedule
                self.assertEqual([sum(r) for r in t], row_totals)
                self.assertEqual(
                    [sum(t[i][j] for i in range(m)) for j in range(n)], col_totals
                )
                for (i, j), value in fixed.items():
                    self.assertEqual(t[i][j], value)
                value = sum(
                    Fraction(t[i][j]) * coefficients[i][j]
                    for i in range(m)
                    for j in range(n)
                ) / total
                self.assertEqual(value, expected)
                self.assertEqual(bound.current_rate - bound.baseline_rate, expected)

    def test_possible_cell_measurement_zero_blocks_and_lists_all(self):
        # No fixed cells: in a 2x2 with positive totals every cell can be >0.
        payload = {
            "row_totals": [2, 2],
            "column_totals": [2, 2],
            "baseline": {
                "measured_shifts": [[4, 0], [2, 2]],
                "on_time_shifts": [[3, 0], [1, 2]],
            },
            "current": {
                "measured_shifts": [[0, 5], [2, 2]],
                "on_time_shifts": [[0, 4], [2, 1]],
            },
        }
        req = parse_assessment_request(payload)
        with self.assertRaises(MissingMeasurementError) as ctx:
            assess(req)
        cells = {(item["row"], item["column"]): item["missing_in"] for item in ctx.exception.blocked_cells}
        self.assertEqual(set(cells), {(0, 0), (0, 1)})
        self.assertEqual(cells[(0, 0)], ["current"])
        self.assertEqual(cells[(0, 1)], ["baseline"])

    def test_impossible_cell_with_zero_measurement_is_not_blocked(self):
        # Cell (0,1) is fixed to zero and therefore can never be positive.
        payload = {
            "row_totals": [1, 1],
            "column_totals": [1, 1],
            "fixed_cells": [{"row": 0, "column": 1, "shifts": 0}],
            "baseline": {
                "measured_shifts": [[1, 0], [1, 1]],
                "on_time_shifts": [[1, 0], [0, 1]],
            },
            "current": {
                "measured_shifts": [[1, 0], [1, 1]],
                "on_time_shifts": [[0, 0], [1, 0]],
            },
        }
        result = assess(parse_assessment_request(payload))
        self.assertEqual(result.minimum.difference, result.maximum.difference)
        self.assertEqual(result.minimum.schedule[0][1], 0)

    def test_row_sum_mismatch_is_conflict(self):
        req = parse_assessment_request(
            {
                "row_totals": [1, 1],
                "column_totals": [1, 0],
                "baseline": {"measured_shifts": [[1, 0], [1, 0]], "on_time_shifts": [[1, 0], [1, 0]]},
                "current": {"measured_shifts": [[1, 0], [1, 0]], "on_time_shifts": [[1, 0], [1, 0]]},
            }
        )
        with self.assertRaises(ScheduleConflictError):
            assess(req)

    def test_structural_residual_conflict(self):
        payload = {
            "row_totals": [1, 1],
            "column_totals": [2, 0],
            "fixed_cells": [{"row": 0, "column": 0, "shifts": 0}],
            "baseline": {"measured_shifts": [[1, 0], [1, 0]], "on_time_shifts": [[1, 0], [1, 0]]},
            "current": {"measured_shifts": [[1, 0], [1, 0]], "on_time_shifts": [[1, 0], [1, 0]]},
        }
        with self.assertRaises(ScheduleConflictError) as ctx:
            assess(parse_assessment_request(payload))
        codes = {d["code"] for d in ctx.exception.details}
        self.assertIn("residual_transportation_infeasible", codes)

    def test_single_cell_large_total(self):
        payload = {
            "row_totals": [100000],
            "column_totals": [100000],
            "baseline": {"measured_shifts": [[3]], "on_time_shifts": [[1]]},
            "current": {"measured_shifts": [[7]], "on_time_shifts": [[6]]},
        }
        result = assess(parse_assessment_request(payload))
        expected = Fraction(6, 7) - Fraction(1, 3)
        self.assertEqual(result.minimum.difference, expected)
        self.assertEqual(result.maximum.difference, expected)
        self.assertEqual(result.minimum.schedule, [[100000]])

    def test_flexible_aliases_and_object_totals(self):
        payload = {
            "rows": [{"route_category": "A", "shifts": 1}],
            "columns": [{"time_period": "早", "total": 1}],
            "known": {"0,0": 1},
            "base": {"actual_shifts": [[2]], "punctual_shifts": [[1]]},
            "report": {"actual_shifts": [[3]], "punctual_shifts": [[3]]},
        }
        result = assess(parse_assessment_request(payload))
        self.assertEqual(result.minimum.difference, Fraction(1, 2))
        self.assertEqual(result.minimum.schedule, [[1]])

    def test_one_based_coordinate_option(self):
        payload = {
            "row_totals": [1, 1],
            "column_totals": [1, 1],
            "index_base": 1,
            "fixed_cells": [{"row": 1, "column": 1, "shifts": 1}],
            "baseline": {"measured_shifts": [[1, 1], [1, 1]], "on_time_shifts": [[1, 1], [1, 1]]},
            "current": {"measured_shifts": [[1, 1], [1, 1]], "on_time_shifts": [[0, 1], [1, 1]]},
        }
        result = assess(parse_assessment_request(payload))
        self.assertEqual(result.minimum.schedule, [[1, 0], [0, 1]])
        self.assertEqual(result.maximum.schedule, [[1, 0], [0, 1]])

    def test_full_schedule_makes_min_equal_max(self):
        table = [[1, 2], [3, 4]]
        payload = {
            "row_totals": [3, 7],
            "column_totals": [4, 6],
            "schedule_matrix": table,
            "baseline": {"measured_shifts": [[2, 5], [3, 4]], "on_time_shifts": [[1, 4], [2, 2]]},
            "current": {"measured_shifts": [[2, 5], [3, 4]], "on_time_shifts": [[2, 3], [3, 3]]},
        }
        result = assess(parse_assessment_request(payload))
        self.assertEqual(result.minimum.difference, result.maximum.difference)
        self.assertEqual(result.minimum.schedule, table)
        self.assertEqual(result.maximum.schedule, table)

    def test_maximum_dimension_and_total_performance(self):
        rng = random.Random(42)
        m = n = 6
        # Build a random table totaling exactly 100000.
        weights = [rng.randint(1, 100) for _ in range(m * n)]
        sw = sum(weights)
        values = [w * 100000 // sw for w in weights]
        values[0] += 100000 - sum(values)
        table = [values[i * n : (i + 1) * n] for i in range(m)]
        row_totals = [sum(row) for row in table]
        col_totals = [sum(table[i][j] for i in range(m)) for j in range(n)]
        fixed = [
            {"row": i, "column": j, "shifts": table[i][j]}
            for i in range(m)
            for j in range(n)
            if rng.random() < 0.2
        ]
        payload = {
            "row_totals": row_totals,
            "column_totals": col_totals,
            "fixed_cells": fixed,
            "baseline": {
                "measured_shifts": [[rng.randint(1, 50) for _ in range(n)] for _ in range(m)],
                "on_time_shifts": None,
            },
            "current": {
                "measured_shifts": [[rng.randint(1, 50) for _ in range(n)] for _ in range(m)],
            },
        }
        # Add valid on-time matrices after generating measured values.
        payload["baseline"]["on_time_shifts"] = [
            [rng.randint(0, x) for x in row] for row in payload["baseline"]["measured_shifts"]
        ]
        payload["current"]["on_time_shifts"] = [
            [rng.randint(0, x) for x in row] for row in payload["current"]["measured_shifts"]
        ]
        start = time.monotonic()
        result = assess(parse_assessment_request(payload))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 3.0)
        self.assertLessEqual(result.minimum.difference, result.maximum.difference)
        self.assertEqual(sum(map(sum, result.minimum.schedule)), 100000)


class SingleFeasibilitySolveTests(unittest.TestCase):
    """每次请求最多一次排班可行性求解，以及单次求解的掩码正确性。"""

    def _all_measured_period(self, m, n):
        return PeriodMeasurements(
            measured=tuple(tuple([1] * n) for _ in range(m)),
            on_time=tuple(tuple([0] * n) for _ in range(m)),
        )

    def test_each_request_runs_at_most_one_feasibility_solve(self):
        rng = random.Random(20261127)
        for _ in range(80):
            m = rng.randint(1, 3)
            n = rng.randint(1, 3)
            total = rng.randint(1, 6)
            flat = [rng.randint(0, 3) for _ in range(m * n)]
            flat[rng.randrange(len(flat))] += max(0, total - sum(flat))
            table = [flat[i * n : (i + 1) * n] for i in range(m)]
            row_totals = [sum(row) for row in table]
            col_totals = [sum(table[i][j] for i in range(m)) for j in range(n)]
            fixed = {
                (i, j): table[i][j]
                for i in range(m)
                for j in range(n)
                if rng.random() < 0.3
            }
            req = AssessmentRequest(
                row_totals=tuple(row_totals),
                column_totals=tuple(col_totals),
                fixed_cells=fixed,
                baseline=self._all_measured_period(m, n),
                current=self._all_measured_period(m, n),
            )
            reset_solver_stats()
            assess(req)
            self.assertLessEqual(get_solver_stats()["feasibility_solves"], 1)

    def test_missing_measurement_path_uses_one_solve_and_no_bound_solves(self):
        payload = {
            "row_totals": [2, 2],
            "column_totals": [2, 2],
            "baseline": {
                "measured_shifts": [[4, 0], [2, 2]],
                "on_time_shifts": [[3, 0], [1, 2]],
            },
            "current": {
                "measured_shifts": [[0, 5], [2, 2]],
                "on_time_shifts": [[0, 4], [2, 1]],
            },
        }
        reset_solver_stats()
        with self.assertRaises(MissingMeasurementError):
            assess(parse_assessment_request(payload))
        self.assertEqual(
            get_solver_stats(),
            {"feasibility_solves": 1, "min_cost_solves": 0},
        )

    def test_structural_conflict_uses_one_solve_and_arithmetic_conflict_none(self):
        structural = {
            "row_totals": [1, 1],
            "column_totals": [2, 0],
            "fixed_cells": [{"row": 0, "column": 0, "shifts": 0}],
            "baseline": {
                "measured_shifts": [[1, 0], [1, 0]],
                "on_time_shifts": [[1, 0], [1, 0]],
            },
            "current": {
                "measured_shifts": [[1, 0], [1, 0]],
                "on_time_shifts": [[1, 0], [1, 0]],
            },
        }
        reset_solver_stats()
        with self.assertRaises(ScheduleConflictError):
            assess(parse_assessment_request(structural))
        self.assertEqual(get_solver_stats()["feasibility_solves"], 1)

        arithmetic = {
            "row_totals": [1, 1],
            "column_totals": [1, 0],
            "baseline": {
                "measured_shifts": [[1, 0], [1, 0]],
                "on_time_shifts": [[1, 0], [1, 0]],
            },
            "current": {
                "measured_shifts": [[1, 0], [1, 0]],
                "on_time_shifts": [[1, 0], [1, 0]],
            },
        }
        reset_solver_stats()
        with self.assertRaises(ScheduleConflictError):
            assess(parse_assessment_request(arithmetic))
        self.assertEqual(get_solver_stats()["feasibility_solves"], 0)

    def test_single_solve_mask_matches_enumeration_exhaustively(self):
        from app.core import analyze_schedule_feasibility

        rng = random.Random(11)
        checked = 0
        for m in (1, 2, 3):
            for n in (1, 2, 3):
                for row_totals in itertools.product(range(0, 4), repeat=m):
                    for col_totals in itertools.product(range(0, 4), repeat=n):
                        if sum(row_totals) != sum(col_totals) or sum(row_totals) == 0:
                            continue
                        fixed = {
                            (i, j): rng.randint(0, 2)
                            for i in range(m)
                            for j in range(n)
                            if rng.random() < 0.4
                        }
                        req = AssessmentRequest(
                            row_totals=row_totals,
                            column_totals=col_totals,
                            fixed_cells=fixed,
                            baseline=self._all_measured_period(m, n),
                            current=self._all_measured_period(m, n),
                        )
                        tables = enumerate_tables(row_totals, col_totals, fixed)
                        if not tables:
                            continue
                        reset_solver_stats()
                        analysis = analyze_schedule_feasibility(req)
                        self.assertTrue(analysis.feasible)
                        brute_mask = [
                            [any(t[i][j] > 0 for t in tables) for j in range(n)]
                            for i in range(m)
                        ]
                        self.assertEqual(analysis.possible_positive, brute_mask)
                        # 残量为 0（全固定表）时无需任何最大流；其余情形恰好 1 次。
                        self.assertLessEqual(
                            get_solver_stats()["feasibility_solves"], 1
                        )
                        checked += 1
        self.assertGreater(checked, 200)

    def test_impossible_unknown_cells_are_pruned_without_changing_bounds(self):
        # 第 2 行残量为 0、第 2 列残量为 0：未知格 (1,*)、(*,1) 恒为 0。
        payload = {
            "row_totals": [2, 0],
            "column_totals": [1, 1],
            "fixed_cells": [{"row": 0, "column": 0, "shifts": 1}],
            "baseline": {
                "measured_shifts": [[4, 2], [3, 4]],
                "on_time_shifts": [[3, 1], [1, 2]],
            },
            "current": {
                "measured_shifts": [[2, 5], [2, 2]],
                "on_time_shifts": [[1, 4], [2, 1]],
            },
        }
        req = parse_assessment_request(payload)
        possible = find_possible_positive_cells(req)
        self.assertEqual(possible, [[True, True], [False, False]])

        result = assess(req)
        # 见证表从不使用被判定为不可能的格。
        for bound in (result.minimum, result.maximum):
            table = bound.schedule
            self.assertEqual(table[1], [0, 0])
            self.assertEqual(table[0][1] + table[1][1], 1)
            self.assertEqual([sum(r) for r in table], [2, 0])
            self.assertEqual(
                [sum(table[i][j] for i in range(2)) for j in range(2)], [1, 1]
            )

        # 与穷举结果一致。
        coefficients = [
            [
                Fraction(req.current.on_time[i][j], req.current.measured[i][j])
                - Fraction(req.baseline.on_time[i][j], req.baseline.measured[i][j])
                for j in range(2)
            ]
            for i in range(2)
        ]
        feasible = enumerate_tables(req.row_totals, req.column_totals, req.fixed_cells)
        brute = sorted(
            sum(
                (Fraction(t[i][j]) * coefficients[i][j] for i in range(2) for j in range(2)),
                Fraction(0),
            )
            / 2
            for t in feasible
        )
        self.assertEqual(result.minimum.difference, brute[0])
        self.assertEqual(result.maximum.difference, brute[-1])

    def test_resupplying_measurement_flips_rejection_into_exact_result(self):
        missing_payload = {
            "row_totals": [2, 2],
            "column_totals": [2, 2],
            "baseline": {
                "measured_shifts": [[4, 4], [4, 4]],
                "on_time_shifts": [[3, 2], [1, 2]],
            },
            "current": {
                "measured_shifts": [[0, 5], [4, 4]],
                "on_time_shifts": [[0, 4], [3, 1]],
            },
        }
        with self.assertRaises(MissingMeasurementError) as ctx:
            assess(parse_assessment_request(missing_payload))
        self.assertEqual(
            {(c["row"], c["column"]) for c in ctx.exception.blocked_cells},
            {(0, 0)},
        )

        resupplied = dict(missing_payload)
        resupplied["current"] = {
            "measured_shifts": [[4, 5], [4, 4]],
            "on_time_shifts": [[2, 4], [3, 1]],
        }
        req = parse_assessment_request(resupplied)
        coefficients = [
            [
                Fraction(req.current.on_time[i][j], req.current.measured[i][j])
                - Fraction(req.baseline.on_time[i][j], req.baseline.measured[i][j])
                for j in range(2)
            ]
            for i in range(2)
        ]
        feasible = enumerate_tables(req.row_totals, req.column_totals, req.fixed_cells)
        brute = sorted(
            sum(
                (Fraction(t[i][j]) * coefficients[i][j] for i in range(2) for j in range(2)),
                Fraction(0),
            )
            / 4
            for t in feasible
        )
        result = assess(req)
        self.assertEqual(result.minimum.difference, brute[0])
        self.assertEqual(result.maximum.difference, brute[-1])
        reset_solver_stats()
        assess(req)
        self.assertEqual(
            get_solver_stats(),
            {"feasibility_solves": 1, "min_cost_solves": 2},
        )


if __name__ == "__main__":
    unittest.main()
