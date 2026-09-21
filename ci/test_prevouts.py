"""Public-API acquisition and cache failures must never become evidence."""

from io import BytesIO
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from bitcoin.core import CBlock, CBlockHeader, b2lx, lx
from block_evidence import read_transaction, sha256d
from prevouts import (
    PARENT_TXIDS_LIMIT, decode_parent_header, decode_parent_txids, decode_previous,
    fetch_previous, load_canonical_hash, load_confirmation, load_parent_txids, load_previous,
)
from test_block_evidence import transaction


class PrevoutChecks(unittest.TestCase):
    def setUp(self):
        self.wire, self.stripped = transaction(witness=True)
        self.txid = b2lx(sha256d(self.stripped))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.spending = read_transaction(transaction(sha256d(self.stripped), 0)[0])
        self.coinbase = read_transaction(transaction(amount=2)[0])

    def test_transaction_identity_and_full_consumption(self):
        """Accept only complete transaction bytes matching the requested txid."""
        self.assertEqual(decode_previous(self.wire, self.txid).serialize({"include_witness": False}), self.stripped)
        for data in (self.wire + b"\x00", self.wire[:-1]):
            with self.assertRaises(ValueError):
                decode_previous(data, self.txid)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            decode_previous(self.wire, "00" * 32)

    @patch("prevouts.time.sleep")
    def test_api_download_and_provider_fallback(self, sleep):
        """Retry an unavailable provider before fetching valid bytes from the next API."""
        responses = [URLError("down"), URLError("down"), BytesIO(self.wire.hex().encode())]
        with patch("prevouts.urlopen", side_effect=responses) as opener:
            tx = fetch_previous(self.txid, ("https://one.example/api", "https://two.example/api"))
        self.assertEqual(tx.serialize({"include_witness": False}), self.stripped)
        self.assertIn("two.example", opener.call_args.args[0].full_url)
        self.assertEqual(opener.call_count, 3)

    @patch("prevouts.time.sleep")
    def test_unavailable_api_fails(self, sleep):
        """Exhausted downloads fail explicitly instead of supplying incomplete evidence."""
        with patch("prevouts.urlopen", side_effect=URLError("down")):
            with self.assertRaisesRegex(ValueError, "could not download previous transaction"):
                fetch_previous(self.txid, ("https://one.example/api",))

    @patch("prevouts.time.sleep")
    def test_cache_lifecycle(self, sleep):
        """Download stripped bytes, reuse verified hits and reject missing or corrupt entries."""
        txs = [self.coinbase, self.spending]
        with self.subTest(case="offline miss"), self.assertRaisesRegex(ValueError, "missing cached"):
            load_previous(txs, self.cache)
        path = self.cache / f"{self.txid}.bin"
        with self.subTest(case="download"), patch("prevouts.urlopen", return_value=BytesIO(self.wire.hex().encode())):
            load_previous(txs, self.cache, fetch=True)
            self.assertEqual(path.read_bytes(), self.stripped)
            self.assertEqual(len(list(self.cache.iterdir())), 1)
        with self.subTest(case="cache hit"), patch("prevouts.urlopen", side_effect=AssertionError("network on a cache hit")):
            self.assertIn(lx(self.txid), load_previous(txs, self.cache, fetch=True))
        path.write_bytes(transaction(amount=2)[1])
        with self.subTest(case="corrupt entry"), self.assertRaisesRegex(ValueError, "identity mismatch"):
            load_previous(txs, self.cache, fetch=True)

    @patch("prevouts.time.sleep")
    def test_unconfirmed_status_is_not_evidence_or_cached(self, sleep):
        """Decode a confirmed status reply; an unconfirmed one fails and is not written to the cache."""
        path = self.cache / f"{self.txid}.status.json"
        with patch("prevouts.urlopen", return_value=BytesIO(b'{"confirmed":false}')):
            with self.assertRaisesRegex(ValueError, "not confirmed"):
                load_confirmation(self.txid, self.cache, fetch=True)
        self.assertFalse(path.exists())
        confirmed = json.dumps({"confirmed": True, "block_height": 10, "block_hash": "AB" * 32}).encode()
        with patch("prevouts.urlopen", return_value=BytesIO(confirmed)):
            self.assertEqual(load_confirmation(self.txid, self.cache, fetch=True), (10, "ab" * 32))

    @patch("prevouts.time.sleep")
    def test_malformed_block_hash_is_not_cached(self, sleep):
        """Accept only a 64-hex block-height reply; anything else fails and is not written to the cache."""
        with patch("prevouts.urlopen", return_value=BytesIO(b"Block not found")):
            with self.assertRaisesRegex(ValueError, "malformed block hash"):
                load_canonical_hash(5, self.cache, fetch=True)
        self.assertFalse((self.cache / "height-5.hash").exists())
        with patch("prevouts.urlopen", return_value=BytesIO(b"AB" * 32)):
            self.assertEqual(load_canonical_hash(5, self.cache, fetch=True), "ab" * 32)

    @patch("prevouts.time.sleep")
    def test_parent_txid_cache_authenticates_header_and_merkle_root(self, sleep):
        """Download authentic parent evidence, reuse it offline and reject corrupt or absent lists."""
        txids = [b2lx(self.coinbase.GetTxid()), self.txid]
        header = CBlockHeader(hashMerkleRoot=CBlock.build_merkle_tree_from_txids(
            [self.coinbase.GetTxid(), self.spending.vin[0].prevout.hash])[-1])
        block_hash = b2lx(header.GetHash())
        with patch("prevouts.urlopen", side_effect=[
                BytesIO(header.serialize().hex().encode()), BytesIO(json.dumps(txids).encode())]):
            self.assertEqual(load_parent_txids(block_hash, self.cache, True), txids)
        with patch("prevouts.urlopen", side_effect=AssertionError("network on cache hit")):
            self.assertEqual(load_parent_txids(block_hash, self.cache), txids)
        list_path = self.cache / f"{block_hash}.txids.json"
        list_path.write_text(json.dumps(list(reversed(txids))))
        with self.assertRaisesRegex(ValueError, "merkle root mismatch"):
            load_parent_txids(block_hash, self.cache)
        list_path.unlink()
        with self.assertRaisesRegex(ValueError, "missing cached parent txids"):
            load_parent_txids(block_hash, self.cache)
        with patch("prevouts.urlopen", side_effect=URLError("down")):
            with self.assertRaisesRegex(ValueError, "could not download parent txids"):
                load_parent_txids(block_hash, self.cache, True, ("https://one.example/api",))
        self.assertFalse(list_path.exists())
        with patch("prevouts.urlopen", return_value=BytesIO(json.dumps(txids[::-1]).encode())):
            with self.assertRaisesRegex(ValueError, "merkle root mismatch"):
                load_parent_txids(block_hash, self.cache, True)
        self.assertFalse(list_path.exists())

    def test_parent_evidence_encoding_and_identity(self):
        """Wrong byte order, duplicate leaves and malformed evidence cannot authenticate a parent."""
        header = CBlockHeader(hashMerkleRoot=self.coinbase.GetTxid())
        txid = b2lx(self.coinbase.GetTxid())
        self.assertEqual(decode_parent_txids(json.dumps([txid]).encode(), header), [txid])
        cases = {
            "not an array": b'{}', "empty": b'[]', "bad txid": b'["not-a-txid"]',
            "duplicate leaves": json.dumps([txid, txid]).encode(),
            "wrong byte order": json.dumps([self.coinbase.GetTxid().hex()]).encode(),
            "oversized": b' ' * (PARENT_TXIDS_LIMIT + 1),
        }
        for case, raw in cases.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                decode_parent_txids(raw, header)
        for case, raw, identity in (
                ("truncated", header.serialize()[:-1].hex().encode(), b2lx(header.GetHash())),
                ("wrong hash", header.serialize().hex().encode(), "00" * 32)):
            with self.subTest(case=case), self.assertRaises(ValueError):
                decode_parent_header(raw, identity)


if __name__ == "__main__":
    unittest.main()
