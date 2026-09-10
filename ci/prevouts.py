"""Download previous transactions and omitted-parent confirmation status.

`.bin` cache files are stripped transaction bytes, checked against the spending
input's txid. `.status.json` files are the height and block hash Esplora reports
for that tx on the chain it currently calls canonical — not a historical first
confirmation, and not a UTXO proof. Cache hits are checked the same way as
downloads. A failed download or a corrupt cache file fails validation. An
unconfirmed API reply is not cached and cannot prove the omitted-parent rule.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from http.client import HTTPException, IncompleteRead
import json
from pathlib import Path
import re
import time
import tempfile
from typing import TypeVar
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bitcoin.core import CTransaction, b2lx, lx

from block_evidence import late_confirmation, omitted_prevouts, read_transaction

DEFAULT_APIS = ("https://mempool.space/api", "https://blockstream.info/api")
PREVOUTS_DIR = Path(".cache/prevouts")
WORKERS = 2
T = TypeVar("T")


def _get(url: str, limit: int, label: str) -> bytes:
    """Download the body, capped at limit bytes. Truncated replies raise."""
    request = Request(url, headers={"User-Agent": "invalid-blocks-evidence-check/1"})
    with urlopen(request, timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"oversized {label}")
    # urlopen can return a short body without raising. response.length is the
    # remaining Content-Length.
    remaining = getattr(response, "length", None)
    if remaining:
        raise IncompleteRead(raw, remaining)
    return raw


def _fetch(txid: str, apis: Sequence[str], path: str, limit: int, kind: str,
           decode: Callable[[bytes], T], retry: tuple[type, ...],
           missing_ok: bool = False) -> T:
    """Fetch path from each API, twice, and decode the body.

    retry lists transient errors (OSError for connection setup, HTTPException
    for a reset after headers). A previous-transaction txid mismatch is a
    ValueError and is not retried.

    missing_ok is confirmation-only: HTTP 404 means try the next API, and if
    every API 404s return None. That is not proof the output was missing when
    the block connected. Unconfirmed JSON also returns None from decode().
    """
    failures = []
    not_found = True
    label = f"{kind} response: {txid}"
    for api in apis:
        url = f"{api.rstrip('/')}/{path}"
        for attempt in range(2):
            try:
                result = decode(_get(url, limit, label))
                time.sleep(0.25)
                return result
            except HTTPError as exc:
                failures.append(f"{url}: {exc}")
                if missing_ok and exc.code == 404:
                    break
                not_found = False
                if attempt == 0:
                    time.sleep(2)
            except retry as exc:
                not_found = False
                failures.append(f"{url}: {exc}")
                if attempt == 0:
                    time.sleep(2)
    if missing_ok and failures and not_found:
        return None
    raise ValueError(f"could not download {kind} {txid}: " + "; ".join(failures))


def decode_previous(data: bytes, txid: str) -> CTransaction:
    """Parse the transaction and require its txid to match the requested hex id."""
    transaction = read_transaction(data)
    if b2lx(transaction.GetTxid()) != txid:
        raise ValueError(f"previous transaction identity mismatch: {txid}")
    return transaction


def fetch_previous(txid: str, apis: Sequence[str] = DEFAULT_APIS) -> CTransaction:
    """Download the raw transaction. Retry timeouts; do not retry a txid mismatch.

    Sigops loading runs two of these in parallel. Success waits 0.25s; a
    transient failure waits 2s and tries again.
    """
    def decode(raw: bytes) -> CTransaction:
        return decode_previous(bytes.fromhex(raw.decode("ascii").strip()), txid)

    return _fetch(txid, apis, f"tx/{txid}/hex", 8_000_000, "previous transaction",
                  decode, (OSError, HTTPException))


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


def load_named_previous(txids: Sequence[str], cache_dir: Path | str = PREVOUTS_DIR,
                         fetch: bool = False, apis: Sequence[str] = DEFAULT_APIS) -> dict[bytes, CTransaction]:
    """Return each hex txid from cache, or download it when fetch is set."""
    cache_dir = Path(cache_dir)

    def load(txid: str) -> CTransaction:
        path = cache_dir / f"{txid}.bin"
        if path.exists():
            return decode_previous(path.read_bytes(), txid)
        if not fetch:
            raise ValueError(f"missing cached previous transaction {txid}; run with --fetch-prevouts")
        transaction = fetch_previous(txid, apis)
        _replace(path, transaction.serialize({"include_witness": False}))
        return transaction

    missing = 0
    if fetch:
        missing = sum(not (cache_dir / f"{txid}.bin").exists() for txid in txids)
        print(f"Previous transactions: {len(txids)} required, {missing} to download", flush=True)
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    futures = [pool.submit(load, txid) for txid in txids]
    result = {}
    try:
        for future in as_completed(futures):
            tx = future.result()
            result[tx.GetTxid()] = tx
            if fetch and missing and len(result) % 500 == 0:
                print(f"Previous transactions verified: {len(result)}/{len(txids)}", flush=True)
    finally:
        # Stop the other worker; one bad txid fails the whole set.
        pool.shutdown(wait=True, cancel_futures=True)
    return result


def load_previous(transactions: Sequence[CTransaction], cache_dir: Path | str = PREVOUTS_DIR,
                  fetch: bool = False, apis: Sequence[str] = DEFAULT_APIS) -> dict[bytes, CTransaction]:
    """Load every omitted prevout. In-block spends are counted from the body.

    Every requested txid is required. Offline, a missing cache file fails.
    Online, only a txid-checked stripped transaction is written, via a temp
    file, so a half-finished download cannot become a cache hit.
    """
    needed = sorted(_omitted_display_txids(transactions))
    return load_named_previous(needed, cache_dir, fetch, apis)


def _omitted_display_txids(transactions: Sequence[CTransaction]) -> list[str]:
    """Hex txids of omitted prevouts, unique, in the order they first appear."""
    omitted = []
    seen: set[str] = set()
    for txid, _ in omitted_prevouts(transactions):
        display = b2lx(txid)
        if display not in seen:
            seen.add(display)
            omitted.append(display)
    return omitted


def decode_confirmation(data: bytes, txid: str) -> tuple[int, str]:
    """Read a cached status file as (height, block hash) for txid."""
    try:
        payload = json.loads(data)
    except ValueError as exc:
        raise ValueError(f"malformed confirmation cache entry: {txid}") from exc
    return _confirmation(payload, txid)


def _confirmation(payload: object, txid: str) -> tuple[int, str]:
    if not isinstance(payload, dict):
        raise ValueError(f"malformed confirmation cache entry: {txid}")
    height = payload.get("block_height")
    block_hash = payload.get("block_hash")
    if type(height) is not int or height < 0:
        raise ValueError(f"malformed confirmation height: {txid}")
    if not isinstance(block_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", block_hash):
        raise ValueError(f"malformed confirmation block hash: {txid}")
    return height, block_hash.lower()


def fetch_confirmation(txid: str, apis: Sequence[str] = DEFAULT_APIS) -> tuple[int, str] | None:
    """Return (height, block hash) if the API currently lists the tx as confirmed.

    Unconfirmed JSON returns None. HTTP 404 means try the next API; if every
    API 404s, return None — that is not proof the output was missing when the
    block connected. Timeouts and other download failures raise, same budget
    as fetch_previous.
    """
    def decode(raw: bytes) -> tuple[int, str] | None:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            return None
        return _confirmation(payload, txid)

    return _fetch(txid, apis, f"tx/{txid}/status", 65_536, "confirmation", decode,
                  (OSError, HTTPException, ValueError, UnicodeDecodeError),
                  missing_ok=True)


def load_missing_parent_evidence(
        transactions: Sequence[CTransaction], height: int, block_hash: str,
        cache_dir: Path | str = PREVOUTS_DIR, fetch: bool = False,
        apis: Sequence[str] = DEFAULT_APIS) -> tuple[dict[bytes, CTransaction], dict[bytes, tuple[int, str]]]:
    """Load confirmation height/hash for omitted parent transactions.

    Cached confirmed entries are kept. Unconfirmed API replies are not stored.
    With fetch, request uncached txids two at a time until one is confirmed at
    this height or later in another block, or the list runs out. Without fetch,
    a missing cache file fails unless such a confirmation is already cached.
    """
    omitted = _omitted_display_txids(transactions)
    cache_dir = Path(cache_dir)
    confirmations: dict[bytes, tuple[int, str]] = {}
    pending = []
    for txid in omitted:
        path = cache_dir / f"{txid}.status.json"
        try:
            confirmations[lx(txid)] = decode_confirmation(path.read_bytes(), txid)
        except FileNotFoundError:
            pending.append(txid)

    late = [b2lx(raw) for raw, confirmation in confirmations.items()
            if late_confirmation(confirmation, height, block_hash)]
    if not late and pending:
        if not fetch:
            raise ValueError(f"missing cached confirmation {pending[0]}; run with --fetch-prevouts")
        print(f"Omitted-parent confirmations: {len(omitted)} omitted, {len(pending)} to download",
              flush=True)

        pool = ThreadPoolExecutor(max_workers=WORKERS)
        pending_iter = iter(pending)
        inflight: dict[Future, str] = {}

        def submit_next() -> None:
            txid = next(pending_iter, None)
            if txid is not None:
                inflight[pool.submit(fetch_confirmation, txid, apis)] = txid

        for _ in range(WORKERS):
            submit_next()
        failures = []
        try:
            while inflight:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                future = next(iter(done))
                txid = inflight.pop(future)
                try:
                    confirmation = future.result()
                except ValueError as exc:
                    # Keep walking: a later omitted txid may still prove the rule.
                    failures.append(str(exc))
                    confirmation = None
                if confirmation is not None:
                    _replace(cache_dir / f"{txid}.status.json",
                             json.dumps({"block_height": confirmation[0],
                                         "block_hash": confirmation[1]},
                                        separators=(",", ":")).encode() + b"\n")
                    confirmations[lx(txid)] = confirmation
                    if late_confirmation(confirmation, height, block_hash):
                        late.append(txid)
                        break
                submit_next()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        if not late and failures:
            raise ValueError("; ".join(failures))

    previous = load_named_previous(late, cache_dir, fetch, apis) if late else {}
    return previous, confirmations
