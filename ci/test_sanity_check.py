"""Regression tests for the dataset boundary, independent of catalogue growth."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bitcoin.core import CBlock, CBlockHeader

SPEC = importlib.util.spec_from_file_location("sanity_check", Path(__file__).with_name("sanity-check.py"))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class DatasetChecks(unittest.TestCase):
    def setUp(self):
        self.records = [json.loads(line) for line in CHECK.DATA_PATH.read_text().splitlines()]
        # Structural tests use a header-only rule. Body-level records must no
        # longer pass merely because their supporting bytes were left out.
        self.record = self.for_rule("bip66_block_version_below_3")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def for_rule(self, rule):
        return copy.deepcopy(next(r for r in self.records if r["rule"] == rule))

    def copy_body(self, record):
        blocks = self.root / "blocks"
        blocks.mkdir(exist_ok=True)
        name = f"{record['height']}-{record['hash']}.bin"
        path = blocks / name
        path.write_bytes((CHECK.BLOCKS_DIR / name).read_bytes())
        return path

    def validate(self, records=None):
        path = self.root / "data.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in (records or [self.record])))
        return CHECK.check_dataset(path, self.root / "blocks")[0]

    def test_documented_sigops_breakdowns(self):
        """Reproduce both F2Pool blocks' documented legacy, P2SH and witness costs."""
        # Independent incident regression values. Admission still calculates
        # the cost of every new body; it does not look up an expected count.
        expected = {
            783426: {"legacy": 74520, "p2sh": 160, "witness": 5323, "total": 80003},
            784121: {"legacy": 72204, "p2sh": 908, "witness": 6891, "total": 80003},
        }
        for height, costs in expected.items():
            record = next(r for r in self.records if r["height"] == height)
            path = CHECK.BLOCKS_DIR / f"{height}-{record['hash']}.bin"
            block = CHECK.read_block(path.read_bytes())
            previous = CHECK.load_previous(block.vtx)
            self.assertEqual(CHECK.sigop_cost(block, previous), costs)

    def test_invalid_record_fields(self):
        """Reject malformed fields, mismatched headers, unknown rules and wrong reject strings."""
        cases = (
            ("height boolean", "height", True, "height must be an integer"),
            ("uppercase hex", "hash", self.record["hash"].upper(), "lowercase hex"),
            ("short hex", "header", "00", "must encode 80 bytes"),
            ("blank string", "rule", " ", "non-empty string"),
            ("header mismatch", "header", "00" + self.record["header"][2:], "header hash mismatch"),
            ("unknown rule", "rule", "invented_rule", "unknown rule"),
            ("reject mismatch", "core_reject_reason", "bad-cb-height", "core_reject_reason="),
        )
        for case, field, value, error in cases:
            with self.subTest(case=case), patch.dict(self.record, {field: value}):
                self.assertTrue(any(error in p for p in self.validate()))

    def test_first_error_per_record(self):
        """Stop at each record's first bad field and continue with the next record."""
        first = dict(self.record, height=True, header="invalid")
        second = dict(self.record, nTime="invalid")
        problems = self.validate([first, second])
        self.assertEqual(len(problems), 2)
        self.assertIn("data.jsonl:1: height must be an integer", problems[0])
        self.assertIn("data.jsonl:2: nTime must be an integer", problems[1])

    def test_duplicate_hash_rejected(self):
        """Reject repeated block hashes even when the two records are identical."""
        self.assertTrue(any("duplicate block hash" in p for p in self.validate([self.record, self.record])))

    def test_observations(self):
        """Preserve distinct child blocks and independent sources, but reject exact duplicates."""
        witness = {"channel": "merge_mining", "source": "archive", "provenance": "https://example.org/evidence", "child_chain": "namecoin", "child_height": 1}
        witnesses = [witness, dict(witness, child_height=2), dict(witness, source="independent")]
        cases = (
            ("distinct witnesses", witnesses, None),
            ("duplicate witness", witnesses + [copy.deepcopy(witness)], "duplicate observation"),
        )
        for case, observations, error in cases:
            with self.subTest(case=case):
                self.record["observations"] = observations
                problems = self.validate()
                if error is None:
                    self.assertEqual(problems, [])
                else:
                    self.assertTrue(any(error in p for p in problems))

    def test_rule_context_required(self):
        """Removing any context field required by the rule registry must fail validation."""
        for rule, (_, required, _) in CHECK.RULES.items():
            for field in required:
                with self.subTest(rule=rule, field=field):
                    self.record = self.for_rule(rule)
                    del self.record["context"][field]
                    self.assertTrue(any("requires" in p for p in self.validate()))

    def test_sigops_requires_previous_transactions(self):
        """A complete sigops block still fails admission when previous transactions are missing."""
        self.record = self.for_rule("bad-blk-sigops")
        self.copy_body(self.record)
        path = self.root / "sigops.jsonl"
        path.write_text(json.dumps(self.record) + "\n")
        problems, _ = CHECK.check_dataset(path, self.root / "blocks", self.root / "empty-cache")
        self.assertTrue(any("missing cached previous transaction" in p for p in problems))

    def test_sigops_limit_and_supported_activation(self):
        """Require cost above 80000 and reject pre-SegWit records before fetching evidence."""
        self.record = self.for_rule("bad-blk-sigops")
        for cost, valid in ((80000, False), (80001, True)):
            with patch.object(CHECK, "load_previous", return_value={}), patch.object(
                    CHECK, "sigop_cost", return_value={"total": cost}):
                if valid:
                    CHECK.check_failure_evidence(self.record, CBlock())
                else:
                    with self.assertRaisesRegex(ValueError, "does not exceed"):
                        CHECK.check_failure_evidence(self.record, CBlock())
        self.record["height"] = 481823
        with patch.object(CHECK, "load_previous", side_effect=AssertionError("premature download")):
            with self.assertRaisesRegex(ValueError, "post-SegWit"):
                CHECK.check_failure_evidence(self.record, CBlock())

    def test_body_witness_mutation_is_detected_before_sigop_counting(self):
        """Changed witness bytes must fail commitment checks before prevout acquisition."""
        self.record = self.for_rule("bad-blk-sigops")
        path = self.copy_body(self.record)
        original = path.read_bytes()
        block = CHECK.read_block(original)
        item = next(item for tx in block.vtx[1:] for txinwit in tx.wit.vtxinwit for item in txinwit.scriptWitness.stack if len(item) > 40)
        self.assertEqual(original.count(item), 1)
        path.write_bytes(original.replace(item, bytes([item[0] ^ 1]) + item[1:], 1))
        # Its txid merkle root is unchanged, but the witness commitment fails.
        CHECK.read_block(path.read_bytes())
        with patch.object(CHECK, "load_previous", side_effect=AssertionError("unbound witness evidence")):
            self.assertTrue(any("witness merkle commitment mismatch" in p for p in self.validate()))

    def test_body_evidence_is_required(self):
        """Every body-based rule requires bytes even when observations are omitted."""
        for rule, (_, _, mode) in CHECK.RULES.items():
            if mode == "local":
                continue
            with self.subTest(rule=rule):
                self.record = self.for_rule(rule)
                self.record.pop("observations", None)
                self.assertTrue(any("complete block body" in p for p in self.validate()))

    def test_body_matches_claimed_evidence(self):
        """Bind the named failure and supplied coinbase scriptSig to the available body."""
        self.record = self.for_rule("bad-txns-vout-toolarge")
        self.copy_body(self.record)
        cases = (
            ("wrong failure", {"rule": "bad-txns-inputs-missingorspent",
                               "core_reject_reason": "bad-txns-inputs-missingorspent"}, "does not demonstrate"),
            ("wrong coinbase", {"context": dict(self.record["context"], coinbase_scriptsig_hex="0101")}, "coinbase scriptSig does not match"),
        )
        for case, changes, error in cases:
            with self.subTest(case=case), patch.dict(self.record, changes):
                self.assertTrue(any(error in p for p in self.validate()))

    def test_body_parse_error_is_reported(self):
        """Propagate a parser error without also claiming that the available body is absent."""
        self.record = self.for_rule("bad-txns-vout-toolarge")
        path = self.copy_body(self.record)
        path.write_bytes(path.read_bytes()[:-1])
        problems = self.validate()
        self.assertTrue(any("truncated" in p for p in problems))
        self.assertFalse(any("requires a complete block body" in p for p in problems))

    def test_mtp_comparison_includes_equality(self):
        """Timestamp failure includes equality with parent MTP but excludes later timestamps."""
        self.record = self.for_rule("time_below_mtp")
        for delta, valid in ((-1, False), (0, True), (1, True)):
            with self.subTest(delta=delta):
                self.record["context"]["parent_mtp"] = self.record["nTime"] + delta
                self.assertEqual(not self.validate(), valid)

    def test_retarget_requires_different_valid_target_at_boundary(self):
        """Retarget evidence needs a valid differing expected target at a retarget height."""
        self.record = self.for_rule("nbits_retarget_not_applied")
        original = copy.deepcopy(self.record)
        bits = bytes.fromhex(self.record["header"])[72:76][::-1].hex()
        for expected in (bits, "00000000", "1d80ffff"):
            with self.subTest(expected=expected):
                self.record["context"]["expected_nbits"] = expected
                self.assertTrue(self.validate())
        self.record = original
        self.record["height"] += 1
        self.assertTrue(any("retarget height" in p for p in self.validate()))

    def test_coinbase_length_boundary(self):
        """A 100-byte coinbase scriptSig is insufficient evidence; 101 bytes establishes failure."""
        self.record = self.for_rule("coinbase_scriptsig_length_above_100")
        self.record["context"].pop("coinbase_height")
        for size, valid in ((100, False), (101, True)):
            self.record["context"]["coinbase_scriptsig_hex"] = "00" * size
            self.assertEqual(not self.validate(), valid)

    def test_bip34_requires_failing_prefix_and_consistent_decoded_height(self):
        """BIP34 evidence must agree with decoded height and demonstrate the claimed prefix failure."""
        for rule in ("bip34_v2_coinbase_height_mismatch", "bip34_coinbase_height_mismatch"):
            with self.subTest(rule=rule):
                self.record = self.for_rule(rule)
                self.record["context"]["coinbase_height"] += 1
                self.assertTrue(any("does not match" in p for p in self.validate()))
                self.record["context"].update(coinbase_height=self.record["height"],
                    coinbase_scriptsig_hex=CHECK.height_prefix(self.record["height"]).hex())
                self.assertTrue(any("correct BIP34" in p for p in self.validate()))
        self.record = self.for_rule("bip34_coinbase_height_missing")
        self.record["context"]["coinbase_scriptsig_hex"] = "0101"
        self.assertTrue(any("no decodable height" in p for p in self.validate()))
        self.record = self.for_rule("bip34_coinbase_height_mismatch")
        prefix = CHECK.height_prefix(self.record["height"])
        self.record["context"].update(coinbase_height=self.record["height"],
            coinbase_scriptsig_hex=(b"\x4c" + prefix).hex())
        self.assertEqual(self.validate(), [])  # same number, wrong byte prefix

    def test_version_rules_require_activation_and_old_version(self):
        """Reject version-rule claims before activation or with a sufficiently new version."""
        for rule, (activation, version) in CHECK.VERSION_RULES.items():
            self.record = self.for_rule(rule)
            self.record["height"] = activation - 1
            self.assertTrue(any("requires height" in p for p in self.validate()))
            self.record["height"] = activation
            self.assertEqual(self.validate(), [])
            # Direct predicate check isolates version semantics from PoW.
            with self.assertRaisesRegex(ValueError, "version <"):
                CHECK.check_local_evidence(self.record, CBlockHeader(nVersion=version))

    def test_invalid_targets(self):
        """Reject zero, negative, overflowing and above-limit targets while decoding valid targets."""
        for bits in (0, 0x1D80FFFF, 0x23000001, 0x1E00FFFF, 0x01000001):
            with self.subTest(bits=bits):
                self.assertEqual(CHECK.target_from_bits(bits), 0)
        self.assertEqual(CHECK.target_from_bits(0x1D00FFFF), CHECK.POW_LIMIT)
        self.assertEqual(CHECK.target_from_bits(0x03012345), 0x012345)

    def test_orphan_and_mismatched_block_files(self):
        """Reject binaries without a dataset record or without its matching 80-byte header."""
        blocks = self.root / "blocks"
        blocks.mkdir()
        (blocks / "unknown.bin").write_bytes(b"bad")
        self.assertTrue(any("orphan block file" in p for p in self.validate()))
        (blocks / "unknown.bin").unlink()
        (blocks / f"{self.record['height']}-{self.record['hash']}.bin").write_bytes(b"bad")
        self.assertTrue(any("first 80 bytes" in p for p in self.validate()))


if __name__ == "__main__":
    unittest.main()
