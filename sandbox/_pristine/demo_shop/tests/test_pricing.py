import unittest

from shop.pricing import apply_discount, checkout_total, with_tax


class PricingTests(unittest.TestCase):
    def test_no_discount(self):
        self.assertEqual(apply_discount(100.0, 0), 100.0)

    def test_twenty_percent_off(self):
        self.assertEqual(apply_discount(50.0, 20), 40.0)

    def test_full_discount(self):
        self.assertEqual(apply_discount(80.0, 100), 0.0)

    def test_invalid_percent(self):
        with self.assertRaises(ValueError):
            apply_discount(10.0, 120)

    def test_tax(self):
        self.assertEqual(with_tax(100.0), 118.0)

    def test_checkout_total(self):
        self.assertEqual(checkout_total([30.0, 20.0], 10), 53.1)


if __name__ == "__main__":
    unittest.main()
