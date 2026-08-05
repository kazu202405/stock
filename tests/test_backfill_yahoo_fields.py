import unittest

from backfill_yahoo_fields import normalize_target_codes


class NormalizeTargetCodesTests(unittest.TestCase):
    def test_normalizes_suffix_commas_and_duplicates(self):
        self.assertEqual(
            normalize_target_codes(['7089.T, 164a', '7089']),
            ['7089', '164A'],
        )

    def test_rejects_invalid_code(self):
        with self.assertRaises(ValueError):
            normalize_target_codes(['70890'])


if __name__ == '__main__':
    unittest.main()
