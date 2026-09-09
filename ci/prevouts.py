"""Acquire previous transactions through Esplora-compatible public APIs.

Cache entries contain stripped transaction bytes, sufficient to authenticate
output scripts against the txid committed in a spending input. Every cache hit
is verified too. Downloads and cache corruption fail explicitly; there is no
fallback to a claimed reject string or an unauthenticated scriptPubKey.
"""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPException, IncompleteRead
from pathlib import Path
import time
import tempfile
from urllib.request import Request, urlopen

from bitcoin.core import CTransaction, b2lx

from block_evidence import read_transaction

DEFAULT_APIS = ("https://mempool.space/api", "https://blockstream.info/api")
PREVOUTS_DIR = Path(".cache/prevouts")


def decode_previous(data: bytes, txid: str) -> CTransaction:
    """Authenticate a complete transaction against the requested display txid."""
    transaction = read_transaction(data)
    if b2lx(transaction.GetTxid()) != txid:
        raise ValueError(f"previous transaction identity mismatch: {txid}")
    return transaction


def fetch_previous(txid: str, apis: Sequence[str] = DEFAULT_APIS) -> CTransaction:
    """Fetch raw hex with bounded retries, provider fallback and HTTP timeouts.

    Two workers call this function in parallel. Successful downloads are paced
    and rate-limit/transient failures back off. Identity failures are terminal:
    trying another provider must not conceal a wrong transaction response.
    """
    failures = []
    for api in apis:
        url = f"{api.rstrip('/')}/tx/{txid}/hex"
        for attempt in range(2):
            request = Request(url, headers={"User-Agent": "invalid-blocks-evidence-check/1"})
            try:
                with urlopen(request, timeout=30) as response:
                    # Raw transaction size is bounded for this evidence path;
                    # the limit also catches oversized error responses.
                    raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise ValueError(f"oversized previous transaction response: {txid}")
                # read(amt) can return early without raising on a truncated
                # Content-Length response. HTTPResponse tracks bytes still due.
                remaining = getattr(response, "length", None)
                if remaining:
                    raise IncompleteRead(raw, remaining)
                transaction = decode_previous(bytes.fromhex(raw.decode("ascii").strip()), txid)
                time.sleep(0.25)
                return transaction
            except (OSError, HTTPException) as exc:
                # urllib wraps connection setup errors, but resets and short
                # HTTP reads can escape unwrapped after the response starts.
                failures.append(f"{url}: {exc}")
                if attempt == 0:
                    time.sleep(2)
    raise ValueError(f"could not download previous transaction {txid}: " + "; ".join(failures))


def load_previous(transactions: Sequence[CTransaction], cache_dir: Path | str = PREVOUTS_DIR,
                  fetch: bool = False, apis: Sequence[str] = DEFAULT_APIS) -> dict[bytes, CTransaction]:
    """Resolve external txids; earlier in-block outputs are handled by sigops.

    The complete requested set is required for an exact count. Offline mode
    reports missing evidence. Online mode writes only verified stripped bytes
    using atomic replacement, so interrupted downloads cannot poison the cache.
    """
    own = {tx.GetTxid() for tx in transactions}
    needed = sorted({b2lx(txin.prevout.hash) for tx in transactions[1:]
                     for txin in tx.vin if txin.prevout.hash not in own})
    cache_dir = Path(cache_dir)

    def load(txid: str) -> CTransaction:
        path = cache_dir / f"{txid}.bin"
        if path.exists():
            return decode_previous(path.read_bytes(), txid)
        if not fetch:
            raise ValueError(f"missing cached previous transaction {txid}; run with --fetch-prevouts")
        transaction = fetch_previous(txid, apis)
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(transaction.serialize({"include_witness": False}))
                handle.close()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return transaction

    if fetch:
        missing = sum(not (cache_dir / f"{txid}.bin").exists() for txid in needed)
        print(f"Previous transactions: {len(needed)} required, {missing} to download", flush=True)
    pool = ThreadPoolExecutor(max_workers=2)
    futures = [pool.submit(load, txid) for txid in needed]
    result = {}
    try:
        for future in as_completed(futures):
            tx = future.result()
            result[tx.GetTxid()] = tx
            if fetch and missing and len(result) % 500 == 0:
                print(f"Previous transactions verified: {len(result)}/{len(needed)}", flush=True)
    finally:
        # Do not keep downloading the remaining catalogue after an error.
        pool.shutdown(wait=True, cancel_futures=True)
    return result
