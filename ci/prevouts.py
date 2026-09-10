"""Acquire previous transactions through Esplora-compatible public APIs.

Cache entries contain stripped transaction bytes, sufficient to authenticate
output scripts against the txid committed in a spending input. Status files
record a provider's current canonical confirmation height and block hash for
omitted parents; they are claims about the chain as the API sees it now, not
proofs of historical UTXO state. Every cache hit is verified too. Downloads
and cache corruption fail explicitly; there is no fallback to a claimed
reject string or an unauthenticated scriptPubKey. An unconfirmed response is
not evidence and is not cached.
"""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPException, IncompleteRead
import json
from pathlib import Path
import re
import time
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bitcoin.core import CTransaction, b2lx, lx

from block_evidence import omitted_prevouts, read_transaction

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
    """Resolve the requested display txids from cache or, if permitted, APIs."""
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
    pool = ThreadPoolExecutor(max_workers=2)
    futures = [pool.submit(load, txid) for txid in txids]
    result = {}
    try:
        for future in as_completed(futures):
            tx = future.result()
            result[tx.GetTxid()] = tx
            if fetch and missing and len(result) % 500 == 0:
                print(f"Previous transactions verified: {len(result)}/{len(txids)}", flush=True)
    finally:
        # Do not keep downloading the remaining catalogue after an error.
        pool.shutdown(wait=True, cancel_futures=True)
    return result


def load_previous(transactions: Sequence[CTransaction], cache_dir: Path | str = PREVOUTS_DIR,
                  fetch: bool = False, apis: Sequence[str] = DEFAULT_APIS) -> dict[bytes, CTransaction]:
    """Resolve external txids; earlier in-block outputs are handled by sigops.

    The complete requested set is required for an exact count. Offline mode
    reports missing evidence. Online mode writes only verified stripped bytes
    using atomic replacement, so interrupted downloads cannot poison the cache.
    """
    needed = sorted({b2lx(txid) for txid, _ in omitted_prevouts(transactions)})
    return load_named_previous(needed, cache_dir, fetch, apis)


def decode_confirmation(data: bytes, txid: str) -> tuple[int, str]:
    """Parse a cached canonical confirmation height and block hash for txid."""
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
    """Fetch current canonical confirmation status. Unconfirmed responses return None.

    A 404 is not proof that the output was missing at connect time; it is an
    endpoint gap and the next provider is tried. Network failures raise after
    the same retry/fallback budget as previous-transaction downloads.
    """
    failures = []
    not_found = True
    for api in apis:
        url = f"{api.rstrip('/')}/tx/{txid}/status"
        for attempt in range(2):
            request = Request(url, headers={"User-Agent": "invalid-blocks-evidence-check/1"})
            try:
                with urlopen(request, timeout=30) as response:
                    raw = response.read(65_537)
                if len(raw) > 65_536:
                    raise ValueError(f"oversized confirmation response: {txid}")
                remaining = getattr(response, "length", None)
                if remaining:
                    raise IncompleteRead(raw, remaining)
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("confirmed") is not True:
                    time.sleep(0.25)
                    return None
                confirmation = _confirmation(payload, txid)
                time.sleep(0.25)
                return confirmation
            except HTTPError as exc:
                failures.append(f"{url}: {exc}")
                if exc.code == 404:
                    break
                not_found = False
                if attempt == 0:
                    time.sleep(2)
            except (OSError, HTTPException, ValueError, UnicodeDecodeError) as exc:
                not_found = False
                failures.append(f"{url}: {exc}")
                if attempt == 0:
                    time.sleep(2)
    if failures and not_found:
        return None
    raise ValueError(f"could not download confirmation {txid}: " + "; ".join(failures))


def load_missing_parent_evidence(
        transactions: Sequence[CTransaction], height: int, block_hash: str,
        cache_dir: Path | str = PREVOUTS_DIR, fetch: bool = False,
        apis: Sequence[str] = DEFAULT_APIS) -> tuple[dict[bytes, CTransaction], dict[bytes, tuple[int, str]]]:
    """Load canonical confirmation evidence for omitted prevouts.

    Cached confirmed parents are always returned. Unconfirmed API responses
    are skipped and not stored. When fetch is set, remaining omitted txids are
    requested until a parent confirmed at or after height in a different
    block is authenticated, or the set is exhausted. Offline mode does not
    raise for uncached omitted txids; the caller decides whether the loaded
    subset proves the rule.
    """
    omitted = []
    seen: set[str] = set()
    for txid, _ in omitted_prevouts(transactions):
        display = b2lx(txid)
        if display not in seen:
            seen.add(display)
            omitted.append(display)
    cache_dir = Path(cache_dir)
    confirmations: dict[bytes, tuple[int, str]] = {}
    pending = []
    for txid in omitted:
        path = cache_dir / f"{txid}.status.json"
        try:
            confirmations[lx(txid)] = decode_confirmation(path.read_bytes(), txid)
        except FileNotFoundError:
            pending.append(txid)

    def late_confirmation(confirmation: tuple[int, str]) -> bool:
        confirmed_height, confirmed_hash = confirmation
        return confirmed_height >= height and confirmed_hash != block_hash

    late = [b2lx(raw) for raw, confirmation in confirmations.items()
            if late_confirmation(confirmation)]
    if fetch and not late and pending:
        print(f"Omitted-parent confirmations: {len(omitted)} omitted, {len(pending)} to download",
              flush=True)

        pool = ThreadPoolExecutor(max_workers=2)
        futures = {pool.submit(fetch_confirmation, txid, apis): txid for txid in pending}
        failures = []
        try:
            for future in as_completed(futures):
                txid = futures[future]
                try:
                    confirmation = future.result()
                except ValueError as exc:
                    failures.append(str(exc))
                    continue
                if confirmation is None:
                    continue
                _replace(cache_dir / f"{txid}.status.json",
                         json.dumps({"block_height": confirmation[0], "block_hash": confirmation[1]},
                                    separators=(",", ":")).encode() + b"\n")
                confirmations[lx(txid)] = confirmation
                if late_confirmation(confirmation):
                    late.append(txid)
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        if not late and failures:
            raise ValueError("; ".join(failures))

    previous = load_named_previous(late, cache_dir, fetch, apis) if late else {}
    return previous, confirmations
