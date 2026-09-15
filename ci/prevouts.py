"""Fetch transactions, confirmations, canonical hashes and authenticated parent txid lists.

`{txid}.bin` is a stripped transaction, checked against its txid.
`{txid}.status.json` is the Esplora status reply for that transaction, and
`height-{n}.hash` is the block hash Esplora reports at height n. Cache hits
are decoded and checked like downloads. Failed downloads and corrupt files
fail the run, and a reply is stored only after it decodes as evidence, so an
unconfirmed status is never cached.
Parent headers are cached as hex in `{blockhash}.header`; their ordered
`{blockhash}.txids.json` lists must reproduce the hash-verified header's merkle root.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPException, IncompleteRead
import json
from pathlib import Path
import re
import time
import tempfile
from typing import TypeVar
from urllib.request import Request, urlopen

from bitcoin.core import CBlock, CBlockHeader, CTransaction, b2lx, lx

from block_evidence import omitted_prevouts, read_transaction

DEFAULT_APIS = ("https://mempool.space/api", "https://blockstream.info/api")
PREVOUTS_DIR = Path(".cache/prevouts")
BLOCK_HASH = re.compile(r"[0-9a-fA-F]{64}")
TXID = re.compile(r"[0-9a-f]{64}")
PARENT_TXIDS_LIMIT = 2 * 1024 * 1024
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
    needed = sorted({b2lx(txid) for txid, _ in omitted_prevouts(transactions)})
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


def decode_confirmation(data: bytes, txid: str) -> tuple[int, str]:
    """Read an Esplora status reply as (height, block hash); unconfirmed is not evidence."""
    try:
        payload = json.loads(data)
    except ValueError as exc:
        raise ValueError(f"malformed confirmation: {txid}") from exc
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
        raise ValueError(f"parent transaction {txid} is not confirmed")
    height, block_hash = payload.get("block_height"), payload.get("block_hash")
    if type(height) is not int or height < 0 or not isinstance(block_hash, str) or not BLOCK_HASH.fullmatch(block_hash):
        raise ValueError(f"malformed confirmation: {txid}")
    return height, block_hash.lower()


def load_confirmation(txid: str, cache_dir: Path | str = PREVOUTS_DIR, fetch: bool = False,
                      apis: Sequence[str] = DEFAULT_APIS) -> tuple[int, str]:
    """Return where the API reported txid confirmed, as (height, block hash)."""
    what = f"confirmation {txid}"
    return _cached(what, Path(cache_dir) / f"{txid}.status.json", lambda data: decode_confirmation(data, txid),
                   fetch, lambda: _fetch(what, apis, f"tx/{txid}/status", 65_536))


def decode_block_hash(data: bytes, height: int) -> str:
    """Read a block-height reply as a lowercase block hash."""
    text = data.decode("ascii", errors="replace").strip()
    if not BLOCK_HASH.fullmatch(text):
        raise ValueError(f"malformed block hash for height {height}")
    return text.lower()


def load_canonical_hash(height: int, cache_dir: Path | str = PREVOUTS_DIR, fetch: bool = False,
                        apis: Sequence[str] = DEFAULT_APIS) -> str:
    """Return the block hash the API currently reports at height."""
    what = f"block hash for height {height}"
    return _cached(what, Path(cache_dir) / f"height-{height}.hash", lambda data: decode_block_hash(data, height),
                   fetch, lambda: _fetch(what, apis, f"block-height/{height}", 256))


def decode_parent_header(data: bytes, block_hash: str) -> CBlockHeader:
    """Decode an Esplora hex header and bind it to the requested parent hash."""
    if len(data) > 256:
        raise ValueError("oversized parent header")
    raw = bytes.fromhex(data.decode("ascii", errors="replace").strip())
    if len(raw) != 80:
        raise ValueError("parent header must encode 80 bytes")
    header = CBlockHeader.deserialize(raw)
    if b2lx(header.GetHash()) != block_hash:
        raise ValueError("parent header identity mismatch")
    return header


def decode_parent_txids(data: bytes, header: CBlockHeader) -> list[str]:
    """Authenticate a complete, ordered txid list against the parent merkle root."""
    if len(data) > PARENT_TXIDS_LIMIT:
        raise ValueError("oversized parent txid list")
    txids = json.loads(data)
    if not isinstance(txids, list) or not txids or any(
            not isinstance(txid, str) or not TXID.fullmatch(txid) for txid in txids):
        raise ValueError("parent txid list must be a nonempty array of lowercase 64-hex strings")
    # Repeated leaves can preserve a merkle root under Bitcoin's odd-leaf padding.
    if len(set(txids)) != len(txids):
        raise ValueError("duplicate transaction IDs in parent evidence")
    if CBlock.build_merkle_tree_from_txids([lx(txid) for txid in txids])[-1] != header.hashMerkleRoot:
        raise ValueError("parent transaction merkle root mismatch")
    return txids


def load_parent_txids(block_hash: str, cache_dir: Path | str = PREVOUTS_DIR, fetch: bool = False,
                      apis: Sequence[str] = DEFAULT_APIS) -> list[str]:
    """Load a hash-verified parent header and its merkle-authenticated txid list."""
    cache = Path(cache_dir)
    header = _cached(f"parent header {block_hash}", cache / f"{block_hash}.header",
                     lambda data: decode_parent_header(data, block_hash), fetch,
                     lambda: _fetch(f"parent header {block_hash}", apis, f"block/{block_hash}/header", 256))
    return _cached(f"parent txids {block_hash}", cache / f"{block_hash}.txids.json",
                   lambda data: decode_parent_txids(data, header), fetch,
                   lambda: _fetch(f"parent txids {block_hash}", apis, f"block/{block_hash}/txids", PARENT_TXIDS_LIMIT))
