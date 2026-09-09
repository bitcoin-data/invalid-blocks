"""Public-API acquisition and cache failures must never become evidence."""

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from block_evidence import read_transaction, sha256d
from prevouts import decode_previous, fetch_previous, load_previous
from test_block_evidence import transaction


class PrevoutChecks(unittest.TestCase):
    def setUp(self):
        self.wire, self.stripped = transaction(witness=True)
        self.txid = sha256d(self.stripped)[::-1].hex()
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
            self.assertIn(bytes.fromhex(self.txid)[::-1], load_previous(txs, self.cache, fetch=True))
        path.write_bytes(transaction(amount=2)[1])
        with self.subTest(case="corrupt entry"), self.assertRaisesRegex(ValueError, "identity mismatch"):
            load_previous(txs, self.cache, fetch=True)


if __name__ == "__main__":
    unittest.main()
