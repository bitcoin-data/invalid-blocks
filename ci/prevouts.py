"""Acquire previous transactions through Esplora-compatible public APIs.

Cache entries contain stripped transaction bytes, sufficient to authenticate
output scripts against the txid committed in a spending input. Every cache hit
is verified too. Downloads and cache corruption fail explicitly; there is no
fallback to a claimed reject string or an unauthenticated scriptPubKey.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPException, IncompleteRead
from pathlib import Path
import time
import tempfile
from typing import TypeVar
from urllib.request import Request, urlopen

from bitcoin.core import CTransaction, b2lx

from block_evidence import read_transaction

DEFAULT_APIS = ("https://mempool.space/api", "https://blockstream.info/api")
PREVOUTS_DIR = Path(".cache/prevouts")
T = TypeVar("T")


def _get(url: str, limit: int) -> bytes:
    """Download at most limit bytes. A truncated body raises."""
    request = Request(url, headers={"User-Agent": "invalid-blocks-evidence-check/1"})
    with urlopen(request, timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"oversized response from {url}")
    # urlopen can return a short body without raising. response.length is the
    # remaining Content-Length.
    remaining = getattr(response, "length", None)
    if remaining:
        raise IncompleteRead(raw, remaining)
    return raw


def _fetch(what: str, apis: Sequence[str], path: str, limit: int) -> bytes:
    """Try each API up to twice on transport errors and return the first body."""
    failures = []
    for api in apis:
        url = f"{api.rstrip('/')}/{path}"
        for attempt in range(2):
            try:
                raw = _get(url, limit)
                time.sleep(0.25)
                return raw
            except (OSError, HTTPException) as exc:
                # urllib wraps connection setup errors, but resets and short
                # HTTP reads can escape unwrapped after the response starts.
                failures.append(f"{url}: {exc}")
                if attempt == 0:
                    time.sleep(2)
    raise ValueError(f"could not download {what}: " + "; ".join(failures))


def _replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _cached(what: str, path: Path, decode: Callable[[bytes], T], fetch: bool,
            download: Callable[[], bytes]) -> T:
    """Decode the cached file, or with fetch download it, decode it and then store it."""
    if path.exists():
        return decode(path.read_bytes())
    if not fetch:
        raise ValueError(f"missing cached {what}; run with --fetch-prevouts")
    raw = download()
    value = decode(raw)
    _replace(path, raw)
    return value


def decode_previous(data: bytes, txid: str) -> CTransaction:
    """Parse the transaction and require its txid to match."""
    transaction = read_transaction(data)
    if b2lx(transaction.GetTxid()) != txid:
        raise ValueError(f"previous transaction identity mismatch: {txid}")
    return transaction


def fetch_previous(txid: str, apis: Sequence[str] = DEFAULT_APIS) -> CTransaction:
    """Download the raw transaction. A txid mismatch is terminal, not retried."""
    raw = _fetch(f"previous transaction {txid}", apis, f"tx/{txid}/hex", 8_000_000)
    return decode_previous(bytes.fromhex(raw.decode("ascii").strip()), txid)


def load_transaction(txid: str, cache_dir: Path | str = PREVOUTS_DIR, fetch: bool = False,
                     apis: Sequence[str] = DEFAULT_APIS) -> CTransaction:
    """Return the stripped transaction for one hex txid, from cache or the API."""
    return _cached(f"previous transaction {txid}", Path(cache_dir) / f"{txid}.bin",
                   lambda data: decode_previous(data, txid), fetch,
                   lambda: fetch_previous(txid, apis).serialize({"include_witness": False}))


def load_previous(transactions: Sequence[CTransaction], cache_dir: Path | str = PREVOUTS_DIR,
                  fetch: bool = False, apis: Sequence[str] = DEFAULT_APIS) -> dict[bytes, CTransaction]:
    """Load every external prevout transaction; spends of earlier in-block txs are not fetched."""
    own = {tx.GetTxid() for tx in transactions}
    needed = sorted({b2lx(txin.prevout.hash) for tx in transactions[1:]
                     for txin in tx.vin if txin.prevout.hash not in own})
    cache_dir = Path(cache_dir)
    if fetch:
        missing = sum(not (cache_dir / f"{txid}.bin").exists() for txid in needed)
        print(f"Previous transactions: {len(needed)} required, {missing} to download", flush=True)
    pool = ThreadPoolExecutor(max_workers=2)
    futures = [pool.submit(load_transaction, txid, cache_dir, fetch, apis) for txid in needed]
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
