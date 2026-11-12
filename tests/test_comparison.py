import unittest
from decimal import Decimal
from datetime import date

from dbt_bench.comparison import Column, TableContract, compare_tables


class ComparisonTests(unittest.TestCase):
    def compare(self, g, p, contract=None):
        return compare_tables(g, p, contract or TableContract((Column("id", "integer"), Column("value", "numeric", "0.01")), keys=("id",))).equal

    def test_integer_id_is_exact(self):
        self.assertFalse(self.compare([{"id": 10000, "value": 42.0}], [{"id": 10001, "value": 42.0}]))

    def test_large_integer_not_float(self):
        contract = TableContract((Column("id", "integer"), Column("value", "text")), keys=("id",))
        self.assertFalse(self.compare([{"id": 9007199254740992, "value": "A"}], [{"id": 9007199254740993, "value": "A"}], contract))

    def test_tolerance_aligned_by_key(self):
        c = TableContract((Column("key", "text"), Column("v", "numeric", "0.01")), keys=("key",))
        self.assertTrue(self.compare([{"key": "A", "v": 0.004}, {"key": "B", "v": 0.006}],
                                     [{"key": "A", "v": 0.006}, {"key": "B", "v": 0.004}], c))

    def test_order_is_optional(self):
        c = TableContract((Column("id", "integer"), Column("value", "text")))
        rows = [{"id": 1, "value": "A"}, {"id": 2, "value": "B"}]
        self.assertTrue(self.compare(rows, rows[::-1], c))
        self.assertFalse(self.compare(rows, rows[::-1], TableContract(c.columns, ordered=True)))

    def test_association_permutation_rejected(self):
        self.assertFalse(self.compare([{"id": 1, "value": 5}, {"id": 2, "value": 8}],
                                      [{"id": 1, "value": 8}, {"id": 2, "value": 5}]))

    def test_duplicates_and_missing(self):
        rows = [{"id": 1, "value": 1}, {"id": 2, "value": 2}]
        self.assertFalse(self.compare(rows, [rows[0], rows[0]]))
        self.assertFalse(self.compare(rows, rows[:1]))
        self.assertFalse(self.compare([rows[0], rows[0]], [rows[0], rows[0]]))

    def test_multiset_preserves_duplicates(self):
        c = TableContract((Column("v", "integer"),))
        self.assertFalse(self.compare([{"v": 1}, {"v": 1}, {"v": 2}], [{"v": 1}, {"v": 2}, {"v": 2}], c))
        self.assertTrue(self.compare([{"v": 1}, {"v": 1}], [{"v": 1}, {"v": 1}], c))

    def test_matching_requires_augmenting_path(self):
        c = TableContract((Column("v", "numeric", "1"),))
        # Greedily assigning 1 -> 0 strands gold 0; a valid perfect matching exists.
        self.assertTrue(self.compare([{"v": 1}, {"v": 0}], [{"v": 0}, {"v": 2}], c))

    def test_decimal_money_and_null_nan(self):
        c = TableContract((Column("v", "numeric", "0.005"),))
        self.assertTrue(self.compare([{"v": Decimal("1.0049")}], [{"v": Decimal("1.0000")}], c))
        self.assertFalse(self.compare([{"v": Decimal("1.0051")}], [{"v": Decimal("1.0000")}], c))
        self.assertTrue(self.compare([{"v": None}], [{"v": None}], c))
        self.assertFalse(self.compare([{"v": float("nan")}], [{"v": None}], c))
        self.assertFalse(self.compare([{"v": float("nan")}], [{"v": float("nan")}], c))
        self.assertTrue(self.compare([{"v": float("nan")}], [{"v": float("nan")}], TableContract((Column("v", "numeric", nan_equal=True),))))

    def test_types_columns_and_explicit_limits(self):
        self.assertFalse(self.compare([{"id": 1, "value": 5}], [{"id": True, "value": 5}]))
        self.assertFalse(self.compare([{"id": 1, "value": 5}], [{"id": 1}]))
        self.assertFalse(self.compare([{"id": 1, "value": 5}], [{"id": 1, "value": 5, "extra": 0}]))
        with self.assertRaises(ValueError):
            TableContract((Column("id", "numeric", "0.1"),), keys=("id",))
        with self.assertRaises(ValueError):
            self.compare([{"v": 1}], [{"v": 1}], TableContract((Column("v"),), max_unkeyed_rows=0))

    def test_dates_exact_and_boolean_distinct(self):
        c = TableContract((Column("v", "date"),))
        self.assertFalse(self.compare([{"v": date(2020, 1, 1)}], [{"v": "2020-01-01"}], c))
        self.assertFalse(self.compare([{"v": True}], [{"v": 1}], TableContract((Column("v", "boolean"),))))
