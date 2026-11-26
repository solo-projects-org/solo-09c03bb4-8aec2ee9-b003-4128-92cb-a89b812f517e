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


if __name__ == "__main__":
    unittest.main()
