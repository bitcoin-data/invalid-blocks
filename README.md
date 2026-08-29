# Bitcoin invalid blocks

Dataset of invalid headers and blocks, either observed on the Bitcoin network or recovered from alternative sources, such as chains that merge-mine(d) with Bitcoin.
Each row has valid proof of work and failed a named consensus check.

Split from [stale-blocks](https://github.com/bitcoin-data/stale-blocks); see [stale-blocks#128](https://github.com/bitcoin-data/stale-blocks/pull/128).
A stale header is valid and lost a race.
An invalid header has valid PoW and was rejected on a consensus rule.

## Files

- [`data/invalid-blocks.csv`](data/invalid-blocks.csv) - primary table
- [`data/context.csv`](data/context.csv) - per-hash context
- [`data/observations.csv`](data/observations.csv) - one row per sighting
- [`docs/schema.md`](docs/schema.md) - columns and admission rules
- [`docs/notes.md`](docs/notes.md) - replay behaviour and incident notes
- `blocks/{height}-{hash}.bin` - full block, when we have it

Merge-mined recoveries have a header and usually a coinbase; they do not have a full block (`.bin`).

## Contributing

Add a row to [`data/invalid-blocks.csv`](data/invalid-blocks.csv): the 80-byte header (that fixes `hash`, `prev_hash`, and `nTime`), height `prev + 1`, and a named failure of that header or body (`core_reject_reason` and `rule`).
The header must meet the PoW target encoded in its `nBits`.

Fill [`data/context.csv`](data/context.csv) when the rule cannot be re-checked from the header.
BIP34 needs the coinbase height / scriptSig.
`time_below_mtp` needs `parent_mtp`.
`nbits_retarget_not_applied` needs `expected_nbits`.
`pool` and `parent_kind` can be empty (though the related [mining-pools](https://github.com/bitcoin-data/mining-pools) dataset may be useful for pool assignment).

For header rules, [`data/observations.csv`](data/observations.csv) and the `.bin` are optional.
Add them if you have them.

For body rules, replaying a `.bin` with `bitcoin-cli submitblock` only reproduces context-free failures (74638's `bad-txns-vout-toolarge`); connect-level failures such as `bad-blk-sigops` will not reproduce on a synced node, so the evidence is a [`data/observations.csv`](data/observations.csv) row whose `provenance` URL documents the rejection when it happened (a `debug.log` excerpt or write-up).
Background in [`docs/notes.md`](docs/notes.md).

## CI

```
python ci/sanity-check.py
```

## License

This repository contains both code and data, which are licensed separately:

- **Code** is licensed under the MIT License. See `LICENSE`.
- **Data** (in the CSVs and `blocks/*`) is dedicated to the public domain under CC0 1.0. See `LICENSE-DATA`.
