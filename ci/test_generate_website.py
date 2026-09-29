"""The website links each record to its notes and never trusts record text as markup."""

import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("generate_website", Path(__file__).with_name("generate-website.py"))
SITE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SITE)


class WebsiteChecks(unittest.TestCase):
    def setUp(self):
        self.records = SITE.load_jsonl(SITE.DATA_PATH)

    def for_height(self, height):
        return next(r for r in self.records if r["height"] == height)

    def test_note_links(self):
        """A record links to the note naming its height or a block with its failing spend; sharing a rule is not enough."""
        _, _, incidents = SITE.render_notes(SITE.NOTES_PATH.read_text())
        links = SITE.note_links(self.records, incidents)
        cases = [
            ("height in heading", 783426, "F2Pool sigops"),
            ("same failing spend", 174012, "P2SH redeem-script failure"),
            ("same rule only", 367047, None),
            ("no note", 331673, None),
        ]
        for case, height, title in cases:
            with self.subTest(case=case):
                note = links.get(self.for_height(height)["hash"])
                if title:
                    self.assertIn(title, note["html"])
                else:
                    self.assertIsNone(note)

    def test_relative_links(self):
        """Links rendered from docs resolve to block pages, the notes page or GitHub from the page's depth."""
        record = self.for_height(74638)
        cases = [
            (f"../blocks/{record['height']}-{record['hash']}.bin", f"../../block/{record['hash']}/"),
            ("schema.md#rules", f"{SITE.BLOB_URL}/docs/schema.md#rules"),
            ("../data/invalid-blocks.jsonl", f"{SITE.BLOB_URL}/data/invalid-blocks.jsonl"),
            ("notes.md#reported-blocks", "../../notes/#reported-blocks"),
            ("https://example.com/x.md", "https://example.com/x.md"),
            ("#incident-notes", "#incident-notes"),
        ]
        for link, expected in cases:
            with self.subTest(link=link):
                self.assertEqual(SITE.rewrite_links(f'<a href="{link}">x</a>', "../../"), f'<a href="{expected}">x</a>')

    def test_pages_escape_record_text(self):
        """Record strings are escaped on the block page and in the index's attributes; the hash is in the page title for search."""
        records = copy.deepcopy(self.records)
        position = records.index(self.for_height(783426))
        records[position]["context"]["pool"] = "<script>x</script>"
        records[position]["observations"][0]["source"] = '"><img src=x>'
        rules = SITE.rule_text(SITE.SCHEMA_PATH.read_text())
        evidence = {r["hash"]: SITE.evidence_on_file(r) for r in records}
        _, block = SITE.block_page(position, records, rules, None, evidence[records[position]["hash"]])
        _, index = SITE.index_page(records, [], evidence)
        for page in (block, index):
            self.assertNotIn("<script>x", page)
            self.assertNotIn("<img src=x>", page)
            self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)
        self.assertIn(f"<title>Invalid Bitcoin block 783426 {records[position]['hash']}</title>", block)


if __name__ == "__main__":
    unittest.main()
