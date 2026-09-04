#!/usr/bin/env python3
# Offline checks for the invalid-blocks dataset:
# - primary CSV: columns, types, sort, unique hash, header↔hash, PoW, decoded fields
# - context.csv: optional; at most one row per hash; parent_kind empty or enum
# - observations.csv: optional; channel rules; unique (hash, channel, child_chain)
# - optional blocks/{height}-{hash}.bin start with that row's header; no orphans

import csv
import hashlib
import os
import sys

HEADER_LEN = 80
PRIMARY_PATH = "data/invalid-blocks.csv"
CONTEXT_PATH = "data/context.csv"
OBSERVATIONS_PATH = "data/observations.csv"
BLOCKS_DIR = "blocks"

PRIMARY_COLUMNS = [
    "height",
    "hash",
    "header",
    "prev_hash",
    "nTime",
    "core_reject_reason",
    "rule",
]
CONTEXT_COLUMNS = [
    "hash",
    "expected_nbits",
    "parent_mtp",
    "coinbase_height",
    "coinbase_scriptsig_hex",
    "pool",
    "parent_kind",
]
OBSERVATION_COLUMNS = [
    "hash",
    "channel",
    "source",
    "child_chain",
    "child_height",
    "first_seen",
    "provenance",
]
PARENT_KINDS = {"canonical", "stale", "invalid"}
CHANNELS = {"auxpow", "p2p"}


def dsha256(d):
    return hashlib.sha256(hashlib.sha256(d).digest()).digest()


def target_from_bits(bits):
    # Expand ordinary positive Bitcoin compact nBits values (4 bytes) to a
    # 256-bit target integer. High byte is the size/exponent; low 3 bytes are
    # the mantissa.
    exponent = bits >> 24
    mantissa = bits & 0x007FFFFF
    if exponent <= 3:
        return mantissa >> (8 * (3 - exponent))
    return mantissa << (8 * (exponent - 3))


def header_hash_hex(header_bytes):
    return bytes(reversed(dsha256(header_bytes))).hex()


def try_parse_hex(field, value, expected_len_bytes, context, problems, required=False):
    if value == "":
        if required:
            problems.append(f"{context}: {field} is required but empty")
        return None

    try:
        b = bytes.fromhex(value)
    except ValueError:
        problems.append(f"{context}: {field} is not hex: {value}")
        return None

    if expected_len_bytes is not None and len(b) != expected_len_bytes:
        problems.append(
            f"{context}: {field} has wrong length: expected {expected_len_bytes} bytes, got {len(b)}"
        )
        return None

    return b


def read_csv(path, expected_columns, problems):
    if not os.path.exists(path):
        problems.append(f"{path}: file not found")
        return []

    with open(path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != expected_columns:
            problems.append(f"{path}:1: expected header {expected_columns}, got {header}")
            return []

        rows = []
        for row_i, row in enumerate(reader, start=2):
            if len(row) != len(expected_columns):
                problems.append(
                    f"{path}:{row_i}: expected {len(expected_columns)} columns, got {len(row)}: {row}"
                )
                continue
            rows.append((row_i, row))
        return rows


def check_primary(problems):
    rows = read_csv(PRIMARY_PATH, PRIMARY_COLUMNS, problems)
    primary = {}
    last_key = None

    for row_i, row in rows:
        context = f"{PRIMARY_PATH}:{row_i}"
        try:
            height = int(row[0])
            if height <= 0:
                problems.append(f"{context}: height must be > 0: {height}")
                continue
        except ValueError:
            problems.append(f"{context}: invalid height: {row[0]}")
            continue

        header_hash, header, prev_hash, ntime = row[1], row[2], row[3], row[4]
        core_reject_reason, rule = row[5], row[6]

        if last_key is not None and (height, header_hash) < last_key:
            problems.append(
                f"{context}: file not ordered by height ascending, then hash ascending: "
                f"{last_key} then ({height}, {header_hash})"
            )
        last_key = (height, header_hash)

        try_parse_hex("hash", header_hash, 32, context, problems, required=True)
        header_bytes = try_parse_hex("header", header, HEADER_LEN, context, problems, required=True)

        if header_bytes is not None:
            calculated = header_hash_hex(header_bytes)
            if header_hash != calculated:
                problems.append(f"{context}: header hash mismatch: {header_hash} != {calculated}")
            else:
                bits = int.from_bytes(header_bytes[72:76], "little")
                target = target_from_bits(bits)
                if int(calculated, 16) > target:
                    problems.append(
                        f"{context}: header does not satisfy PoW target: "
                        f"hash {calculated} > target {target:064x} (nBits {bits:08x})"
                    )

            decoded_prev = bytes(reversed(header_bytes[4:36])).hex()
            if prev_hash != decoded_prev:
                problems.append(
                    f"{context}: prev_hash mismatch: {prev_hash} != {decoded_prev} (from header)"
                )

            decoded_ntime = int.from_bytes(header_bytes[68:72], "little")
            try:
                ntime_int = int(ntime)
            except ValueError:
                problems.append(f"{context}: invalid nTime: {ntime}")
                ntime_int = None
            if ntime_int is not None and ntime_int != decoded_ntime:
                problems.append(
                    f"{context}: nTime mismatch: {ntime_int} != {decoded_ntime} (from header)"
                )

        if core_reject_reason == "":
            problems.append(f"{context}: core_reject_reason is required but empty")
        if rule == "":
            problems.append(f"{context}: rule is required but empty")

        if header_hash in primary:
            problems.append(f"The hash {header_hash} appeared more than once. It should only appear once.")
        else:
            primary[header_hash] = {
                "height": height,
                "header": header,
                "row": row_i,
            }

    return primary


def check_context(primary, problems):
    rows = read_csv(CONTEXT_PATH, CONTEXT_COLUMNS, problems)
    seen = {}

    for row_i, row in rows:
        context = f"{CONTEXT_PATH}:{row_i}"
        header_hash, parent_kind = row[0], row[6]

        if header_hash in seen:
            problems.append(f"{context}: duplicate context row for {header_hash}")
        else:
            seen[header_hash] = row_i

        if header_hash not in primary:
            problems.append(f"{context}: hash {header_hash} is not in {PRIMARY_PATH}")

        if parent_kind and parent_kind not in PARENT_KINDS:
            problems.append(
                f"{context}: parent_kind must be one of {sorted(PARENT_KINDS)}, got {parent_kind!r}"
            )

    return len(seen)


def check_observations(primary, problems):
    rows = read_csv(OBSERVATIONS_PATH, OBSERVATION_COLUMNS, problems)
    seen_keys = {}

    for row_i, row in rows:
        context = f"{OBSERVATIONS_PATH}:{row_i}"
        header_hash, channel, child_chain = row[0], row[1], row[3]

        if header_hash not in primary:
            problems.append(f"{context}: hash {header_hash} is not in {PRIMARY_PATH}")

        if channel not in CHANNELS:
            problems.append(f"{context}: channel must be one of {sorted(CHANNELS)}, got {channel!r}")

        key = (header_hash, channel, child_chain)
        if key in seen_keys:
            problems.append(
                f"{context}: duplicate observation for {key}; also {OBSERVATIONS_PATH}:{seen_keys[key]}"
            )
        else:
            seen_keys[key] = row_i

        if channel == "auxpow" and child_chain == "":
            problems.append(f"{context}: auxpow row must have a non-empty child_chain")
        if channel == "p2p" and child_chain != "":
            problems.append(f"{context}: p2p row must have an empty child_chain, got {child_chain!r}")

    return len(rows)


def check_block_files(primary, problems):
    total_blocks = 0
    expected_names = {
        f"{rec['height']}-{header_hash}.bin": (header_hash, rec)
        for header_hash, rec in primary.items()
    }
    seen_names = set()

    if os.path.isdir(BLOCKS_DIR):
        for name in sorted(os.listdir(BLOCKS_DIR)):
            if not name.endswith(".bin"):
                continue
            path = f"{BLOCKS_DIR}/{name}"
            if name not in expected_names:
                problems.append(f"{path}: orphan block file; name must match a primary row")
                continue

            seen_names.add(name)
            header_hash, rec = expected_names[name]
            total_blocks += 1
            with open(path, "rb") as block:
                header_bytes = block.read(HEADER_LEN)
            if len(header_bytes) != HEADER_LEN:
                problems.append(f"{path}: expected {HEADER_LEN} header bytes, got {len(header_bytes)}")
                continue
            file_header = header_bytes.hex()
            if rec["header"] and file_header != rec["header"]:
                problems.append(
                    f"{path}: first {HEADER_LEN} bytes do not match {PRIMARY_PATH} header for {header_hash}"
                )

    return total_blocks


def main():
    problems = []
    primary = check_primary(problems)
    context_rows = check_context(primary, problems)
    observation_rows = check_observations(primary, problems)
    total_blocks = check_block_files(primary, problems)

    if problems:
        print("sanity-check failed:")
        for p in problems:
            print(p)
        sys.exit(1)

    print("sanity-check successful")
    print(
        f"  {len(primary)} primary rows, {context_rows} context rows, "
        f"{observation_rows} observations, {total_blocks} block files"
    )


if __name__ == "__main__":
    main()
