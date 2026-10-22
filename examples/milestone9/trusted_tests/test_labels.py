"""Host-owned acceptance checks, outside the Coder's writable workspace."""

import unittest

from label_tools import normalize_label


class Labels(unittest.TestCase):
    def test_words(self):
        self.assertEqual(normalize_label("  Hello   World  "), "hello_world")

    def test_empty(self):
        self.assertEqual(normalize_label(" \t\n "), "")

    def test_unicode(self):
        self.assertEqual(normalize_label("CAFÉ Menu"), "café_menu")
