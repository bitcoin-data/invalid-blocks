#!/usr/bin/env python3
"""Enforce the dataset's structure and rule-specific evidence contract offline.

RULES is the admission registry: adding a rule requires an explicit reject
string, required context and evidence path, plus tests and schema documentation.
External provenance and the origin of supplied chain context remain reviewed
claims; a syntactically valid URL is never treated as a proof by itself.
"""

from collections.abc import Sequence, Set
import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse

from bitcoin.core import CBlock, CBlockHeader, b2lx
from bitcoin.core._bignum import vch2bn
from bitcoin.core.script import CScript, CScriptInvalidError, OP_1NEGATE
from bitcoin.core.serialize import uint256_from_compact

from block_evidence import (
    MAX_BLOCK_SIGOPS_COST, establishes_rule, read_block,
    sigop_cost, verify_witness_commitment,
)
from prevouts import DEFAULT_APIS, PREVOUTS_DIR, load_previous

DATA_PATH = Path("data/invalid-blocks.jsonl")
BLOCKS_DIR = Path("blocks")
REQUIRED = {"height", "hash", "header", "prev_hash", "nTime", "core_reject_reason", "rule"}
CONTEXT_FIELDS = {
    "expected_nbits", "parent_mtp", "coinbase_height", "coinbase_scriptsig_hex",
    "pool", "parent_kind",
}
OBSERVATION_REQUIRED = {"channel", "source", "provenance"}
CHILD_FIELDS = {"child_chain", "child_height", "child_block_hash", "child_block_time", "child_header"}
OBSERVATION_FIELDS = OBSERVATION_REQUIRED | CHILD_FIELDS | {"first_seen"}
CHANNELS = {"merge_mining", "p2p", "scrape"}
PARENT_KINDS = {"canonical", "stale", "invalid"}
POW_LIMIT = 0xFFFF << (8 * (0x1D - 3))

# Evidence paths: local = checked header/context predicate; body = a complete,
# merkle-bound body proving the failure; sigops = body plus authenticated prevouts.
# Keep the rule names and reject strings aligned with docs/schema.md.
RULES = {
    "bad-txns-vout-toolarge": ("bad-txns-vout-toolarge", (), "body"),
    "bad-blk-sigops": ("bad-blk-sigops", (), "sigops"),
    "bad-txns-inputs-missingorspent": ("bad-txns-inputs-missingorspent", (), "body"),
    "bip34_v2_coinbase_height_mismatch": (
        "bad-cb-height", ("coinbase_height", "coinbase_scriptsig_hex"), "local"),
    "bip34_coinbase_height_mismatch": (
        "bad-cb-height", ("coinbase_height", "coinbase_scriptsig_hex"), "local"),
    "bip34_coinbase_height_missing": ("bad-cb-height", ("coinbase_scriptsig_hex",), "local"),
    "bip66_block_version_below_3": ("bad-version", (), "local"),
    "bip65_block_version_below_4": ("bad-version", (), "local"),
    "coinbase_scriptsig_length_above_100": ("bad-cb-length", ("coinbase_scriptsig_hex",), "local"),
    "time_below_mtp": ("time-too-old", ("parent_mtp",), "local"),
    "nbits_retarget_not_applied": ("bad-diffbits", ("expected_nbits",), "local"),
}
# Mainnet activation heights from Core's src/kernel/chainparams.cpp. Earlier
# BIP34 version-2 enforcement depended on rolling version counts, reviewed in
# the historical source rather than reconstructed from this dataset.
BIP34_HEIGHT = 227931
VERSION_RULES = {"bip66_block_version_below_3": (363725, 3),
                 "bip65_block_version_below_4": (388381, 4)}


def target_from_bits(bits: int) -> int:
    target = uint256_from_compact(bits)
    return target if not bits & 0x00800000 and 0 < target <= POW_LIMIT else 0


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def read_jsonl(path: Path, problems: list[str]) -> list[tuple[str, dict[str, Any]]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        problems.append(f"{path}: {exc}")
        return []
    if b"\r" in raw or (raw and not raw.endswith(b"\n")):
        problems.append(f"{path}: use LF line endings and a final newline")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        problems.append(f"{path}: invalid UTF-8: {exc}")
        return []
    rows = []
    if not lines:
        problems.append(f"{path}: dataset is empty")
    for number, line in enumerate(lines, 1):
        context = f"{path}:{number}"
        try:
            record = json.loads(line, object_pairs_hook=unique_object)
        except ValueError as exc:
            problems.append(f"{context}: invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            problems.append(f"{context}: expected a JSON object")
            continue
        rows.append((context, record))
    return rows


def check_fields(record: dict[str, Any], required: Set[str], allowed: Set[str]) -> None:
    missing = required - record.keys()
    extra = record.keys() - allowed
    if missing:
        raise ValueError(f"missing fields {sorted(missing)}")
    if extra:
        raise ValueError(f"unknown fields {sorted(extra)}")


def integer(record: dict[str, Any], name: str, minimum: int = 0) -> int:
    value = record.get(name)
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def hex_value(record: dict[str, Any], name: str, size: int | None) -> bytes:
    value = record.get(name)
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-f]{2})+", value):
        raise ValueError(f"{name} must be non-empty lowercase hex")
    decoded = bytes.fromhex(value)
    if size is not None and len(decoded) != size:
        raise ValueError(f"{name} must encode {size} bytes")
    return decoded


def string(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def check_context(record: dict[str, Any]) -> None:
    """Validate context encoding and the fields required by the rule registry."""
    details = record.get("context", {})
    if not isinstance(details, dict):
        raise ValueError("context must be an object")
    check_fields(details, set(), CONTEXT_FIELDS)
    for name in ("parent_mtp", "coinbase_height"):
        if name in details:
            integer(details, name)
    for name, size in (("expected_nbits", 4), ("coinbase_scriptsig_hex", None)):
        if name in details:
            hex_value(details, name, size)
    if "pool" in details:
        string(details, "pool")
    if "parent_kind" in details and details["parent_kind"] not in tuple(PARENT_KINDS):
        raise ValueError(f"parent_kind must be one of {sorted(PARENT_KINDS)}")
    required = set(RULES[record["rule"]][1])
    if required - details.keys():
        raise ValueError(f"rule {record['rule']} requires {sorted(required)}")


def check_observations(record: dict[str, Any]) -> int:
    """Check acquisition provenance independently of the rule evidence checks."""
    observations = record.get("observations", [])
    if not isinstance(observations, list):
        raise ValueError("observations must be an array")
    seen = set()
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("observation must be an object")
        check_fields(observation, OBSERVATION_REQUIRED, OBSERVATION_FIELDS)
        for name in OBSERVATION_REQUIRED:
            string(observation, name)
        channel = observation["channel"]
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {sorted(CHANNELS)}")
        if channel == "merge_mining":
            string(observation, "child_chain")
        elif CHILD_FIELDS & observation.keys():
            raise ValueError("child-chain fields require channel=merge_mining")
        for name in ("child_height", "first_seen", "child_block_time"):
            if name in observation:
                integer(observation, name)
        if "child_block_hash" in observation:
            hex_value(observation, "child_block_hash", 32)
        if "child_header" in observation:
            hex_value(observation, "child_header", 80)
        url = urlparse(observation["provenance"])
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("provenance must be an HTTP(S) URL")
        # A chain can commit the same Bitcoin parent at multiple child heights;
        # independent recorders can also observe the same block. Reject only
        # exact duplicate observations, without dropping either kind of evidence.
        key = json.dumps(observation, sort_keys=True, separators=(",", ":"))
        if key in seen:
            raise ValueError("duplicate observation")
        seen.add(key)
    return len(observations)


def script_height(script: bytes) -> int | None:
    """Decode the first script-number push, or None when no height is encoded.

    Decoding is separate from BIP34's exact prefix comparison: a non-minimal
    push can encode the right number and still fail the consensus check.
    """
    try:
        value = next(iter(CScript(script)), None)
    except CScriptInvalidError:
        return None
    if value == OP_1NEGATE:
        return -1
    if type(value) is int:
        return value
    if isinstance(value, bytes) and len(value) <= 5:
        return vch2bn(value)
    return None


def height_prefix(height: int) -> bytes:
    """Serialize CScript() << height, including sign padding and small ints."""
    return bytes(CScript([height]))


def check_local_evidence(record: dict[str, Any], header: CBlockHeader) -> None:
    """Check the claimed failure against already type-checked header/context.

    This proves consistency with supplied height, MTP and expected difficulty;
    it does not authenticate those facts against the parent chain or bind an
    extracted coinbase script to the header without a complete body.
    """
    rule = record["rule"]
    context = record.get("context", {})
    height = record["height"]
    version = header.nVersion
    script = bytes.fromhex(context["coinbase_scriptsig_hex"]) if "coinbase_scriptsig_hex" in context else None
    if script is not None and "coinbase_height" in context:
        if script_height(script) != context["coinbase_height"]:
            raise ValueError("coinbase_height does not match the scriptSig prefix")
    if rule == "time_below_mtp" and record["nTime"] > context["parent_mtp"]:
        raise ValueError("time_below_mtp requires nTime <= parent_mtp")
    if rule == "nbits_retarget_not_applied":
        expected = int(context["expected_nbits"], 16)
        if not target_from_bits(expected):
            raise ValueError("expected_nbits must encode a valid PoW target")
        if height % 2016 or expected == header.nBits:
            raise ValueError("retarget rule requires a retarget height and different nBits")
    if rule in VERSION_RULES:
        activation, minimum = VERSION_RULES[rule]
        if height < activation or version >= minimum:
            raise ValueError(f"{rule} requires height >= {activation} and version < {minimum}")
    if rule.startswith("bip34_"):
        if rule == "bip34_v2_coinbase_height_mismatch":
            if height >= BIP34_HEIGHT or version < 2:
                raise ValueError(f"rollout BIP34 rule requires version >= 2 before height {BIP34_HEIGHT}")
        elif height < BIP34_HEIGHT:
            raise ValueError(f"BIP34 rule requires height >= {BIP34_HEIGHT}")
        if script.startswith(height_prefix(height)):
            raise ValueError("scriptSig has the correct BIP34 height prefix")
        if rule == "bip34_coinbase_height_missing" and script_height(script) is not None:
            raise ValueError("missing-height rule requires no decodable height prefix")
    if rule == "coinbase_scriptsig_length_above_100" and len(script) <= 100:
        raise ValueError("coinbase scriptSig must exceed 100 bytes")


def check_failure_evidence(record: dict[str, Any], block: CBlock | None, prevouts_dir: Path | str = PREVOUTS_DIR,
                           fetch_prevouts: bool = False, apis: Sequence[str] = DEFAULT_APIS) -> None:
    """Require a checked failure; observations cannot substitute for bytes."""
    mode = RULES[record["rule"]][2]
    if mode == "local":
        return
    if block is None:
        raise ValueError(f"{record['rule']} requires a complete block body")
    if mode == "body" and not establishes_rule(block, record["rule"]):
        raise ValueError(f"committed body does not demonstrate {record['rule']}")
    if mode == "sigops":
        # This checker uses BIP16 + BIP141 counting, not pre-SegWit rules.
        if record["height"] < 481824:
            raise ValueError("sigops evidence checker requires post-SegWit mainnet height")
        previous = load_previous(block.vtx, prevouts_dir, fetch_prevouts, apis)
        cost = sigop_cost(block, previous)
        if cost["total"] <= MAX_BLOCK_SIGOPS_COST:
            raise ValueError(f"sigop cost {cost['total']} does not exceed {MAX_BLOCK_SIGOPS_COST}")
        elif fetch_prevouts:
            print(f"{record['height']}: sigop cost {cost['total']} "
                  f"(legacy {cost['legacy']}, P2SH {cost['p2sh']}, witness {cost['witness']})", flush=True)


def check_dataset(path: Path | str = DATA_PATH, blocks_dir: Path | str = BLOCKS_DIR,
                  prevouts_dir: Path | str = PREVOUTS_DIR, fetch_prevouts: bool = False,
                  apis: Sequence[str] = DEFAULT_APIS) -> tuple[list[str], tuple[int, int, int, int]]:
    problems = []
    seen = set()
    remaining_blocks = {block.name: block for block in Path(blocks_dir).glob("*.bin")}
    block_count = 0
    last_key = None
    observation_count = 0
    context_count = 0
    for where, record in read_jsonl(Path(path), problems):
        try:
            check_fields(record, REQUIRED, REQUIRED | {"context", "observations"})
            height = integer(record, "height", minimum=1)
            if height > 0x7fffffff:
                raise ValueError("height exceeds signed 32-bit range")
            timestamp = integer(record, "nTime")
            hex_value(record, "hash", 32)
            header = hex_value(record, "header", 80)
            hex_value(record, "prev_hash", 32)
            string(record, "core_reject_reason")
            rule = string(record, "rule")
            if rule not in RULES:
                raise ValueError(f"unknown rule {rule!r}")
            if record["core_reject_reason"] != RULES[rule][0]:
                raise ValueError(f"rule {rule} requires core_reject_reason={RULES[rule][0]}")
            block_hash = record["hash"]
            block = remaining_blocks.pop(f"{height}-{block_hash}.bin", None)
            block_count += block is not None
            key = (height, block_hash)
            if last_key is not None and key < last_key:
                raise ValueError("records must be ordered by height then hash")
            last_key = key
            if block_hash in seen:
                raise ValueError(f"duplicate block hash {block_hash}")
            seen.add(block_hash)
            parsed_header = CBlockHeader.deserialize(header)
            calculated = b2lx(parsed_header.GetHash())
            if block_hash != calculated:
                raise ValueError("header hash mismatch")
            target = target_from_bits(parsed_header.nBits)
            if not target or int(calculated, 16) > target:
                raise ValueError("header does not satisfy a valid PoW target")
            if record["prev_hash"] != b2lx(parsed_header.hashPrevBlock):
                raise ValueError("prev_hash mismatch with header")
            if timestamp != parsed_header.nTime:
                raise ValueError("nTime mismatch with header")
            check_context(record)
            context_count += bool(record.get("context"))
            observation_count += check_observations(record)
            check_local_evidence(record, parsed_header)
            evidence_block = None
            if block is not None:
                data = block.read_bytes()
                if data[:80] != header:
                    raise ValueError(f"{block}: first 80 bytes do not match dataset header")
                evidence_block = read_block(data)
                if height >= 481824:
                    verify_witness_commitment(evidence_block)
                details = record.get("context", {})
                if "coinbase_scriptsig_hex" in details:
                    if evidence_block.vtx[0].vin[0].scriptSig.hex() != details["coinbase_scriptsig_hex"]:
                        raise ValueError("coinbase scriptSig does not match context")
            check_failure_evidence(record, evidence_block, prevouts_dir, fetch_prevouts, apis)
        except (OSError, ValueError) as exc:
            problems.append(f"{where}: {exc}")
    for block in sorted(remaining_blocks.values()):
        problems.append(f"{block}: orphan block file; name must match a dataset record")
    return problems, (len(seen), context_count, observation_count, block_count)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-prevouts", action="store_true", help="fetch missing sigops prevout evidence from public APIs")
    parser.add_argument("--prevouts-dir", type=Path, default=PREVOUTS_DIR, help="verified transaction cache directory")
    parser.add_argument("--api-url", action="append", help="Esplora API base URL; repeat for fallback providers")
    args = parser.parse_args()
    problems, counts = check_dataset(prevouts_dir=args.prevouts_dir, fetch_prevouts=args.fetch_prevouts,
                                    apis=args.api_url or DEFAULT_APIS)
    if problems:
        print("sanity-check failed:")
        print("\n".join(problems))
        return 1
    print("sanity-check successful")
    print(f"  {counts[0]} blocks, {counts[1]} contexts, {counts[2]} observations, {counts[3]} block files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
