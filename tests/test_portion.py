import unittest

from justenough.portion import (
    DinerGroup,
    Target,
    target_for,
    total_target,
    waste_penalty,
)


class PortionTest(unittest.TestCase):
    def test_single_adult_normal(self):
        target = target_for(DinerGroup(count=1, kind="adult", appetite="normal"))
        self.assertEqual(target.mid, 720)
        self.assertLess(target.lo, target.mid)
        self.assertGreater(target.hi, target.mid)

    def test_appetite_scales_linearly(self):
        normal = target_for(DinerGroup(count=2, kind="adult", appetite="normal"))
        big = target_for(DinerGroup(count=2, kind="adult", appetite="big"))
        self.assertAlmostEqual(big.mid / normal.mid, 1.2, places=3)

    def test_children_need_less_than_adults(self):
        child = target_for(DinerGroup(count=1, kind="child"))
        adult = target_for(DinerGroup(count=1, kind="adult"))
        self.assertLess(child.mid, adult.mid)

    def test_total_target_sums_groups(self):
        groups = [
            DinerGroup(count=3, kind="adult", appetite="normal"),
            DinerGroup(count=1, kind="child", appetite="light"),
        ]
        total = total_target(groups)
        parts = [target_for(group) for group in groups]
        self.assertEqual(total.mid, sum(part.mid for part in parts))

    def test_waste_penalty_prefers_surplus_avoidance(self):
        target = Target(lo=800, mid=900, hi=1000)
        over = waste_penalty(1200, target)
        under = waste_penalty(600, target)
        inside = waste_penalty(900, target)
        self.assertGreater(over, under)  # 点多了罚更重
        self.assertLess(inside, under)
        self.assertAlmostEqual(waste_penalty(1000, target), 0.005556, places=5)

    def test_invalid_group_rejected(self):
        with self.assertRaises(ValueError):
            DinerGroup(count=0)
        with self.assertRaises(ValueError):
            DinerGroup(count=1, kind="alien")
        with self.assertRaises(ValueError):
            DinerGroup(count=1, appetite="huge")


if __name__ == "__main__":
    unittest.main()
