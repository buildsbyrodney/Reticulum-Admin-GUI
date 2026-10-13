"""Integrity checks for the generated, public-domain KJV response library."""
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
LIBRARY = json.loads((ROOT / "lxmf-test-bot" / "kjv.json").read_text(encoding="utf-8"))


class GeneratedKjvTests(unittest.TestCase):
    def test_canonical_entries_are_unique_and_non_empty(self):
        verses = LIBRARY["verses"]
        self.assertEqual(LIBRARY["source"], "midvash/bible-data versions/en/kjv/kjv.json")
        self.assertGreaterEqual(len(verses), 100)
        self.assertEqual(len(verses), len(set(verses)))
        self.assertTrue(all(reference and text.strip() for reference, text in verses.items()))
        self.assertIn("Psalm 23:1", verses)
        self.assertNotIn("Psalms 23:1", verses)

    def test_representative_source_wording_is_preserved(self):
        verses = LIBRARY["verses"]
        self.assertEqual(verses["Genesis 1:1"], "In the beginning God created the heaven and the earth.")
        self.assertIn("For God so loved the world", verses["John 3:16"])
        self.assertIn("The Lord is my shepherd", verses["Psalm 23:1"])
        self.assertIn("Song of Solomon 2:1", verses)
        self.assertIn("1 Corinthians 13:4", verses)
        self.assertIn("2 Timothy 1:7", verses)
        self.assertIn("1 John 4:8", verses)
        self.assertIn("Revelation 21:4", verses)


if __name__ == "__main__":
    unittest.main()
