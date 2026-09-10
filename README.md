# Bitcoin invalid blocks

Dataset of invalid headers and blocks observed on the Bitcoin network or recovered from other sources, including chains that merge-mine with Bitcoin and archived block explorers.
Each record has valid proof of work and evidence of a named consensus failure.

Split from [stale-blocks](https://github.com/bitcoin-data/stale-blocks); see [stale-blocks#128](https://github.com/bitcoin-data/stale-blocks/pull/128).
A stale block passes the applicable consensus rules but is outside the active chain.
This dataset covers blocks that fail those rules, including failures that can be established from their headers alone.

## Files

- [`data/invalid-blocks.jsonl`](data/invalid-blocks.jsonl): one JSON object per Bitcoin header hash, with optional context and an array of observations.
- [`docs/schema.md`](docs/schema.md): fields and admission rules.
- [`docs/notes.md`](docs/notes.md): replay behaviour and incident notes.
- `blocks/{height}-{hash}.bin`: full block, when available.

Merge-mined recoveries generally provide a header and coinbase rather than a full Bitcoin block.

## Contributing

Add one record to [`data/invalid-blocks.jsonl`](data/invalid-blocks.jsonl), sorted by height then hash.
Include the 80-byte header, its decoded hash, parent hash and timestamp, height `prev + 1`, and a named consensus failure (`core_reject_reason` and `rule`).
The header must meet the PoW target encoded in its `nBits`.

Include `context` fields needed to establish the failure: BIP34 coinbase height and scriptSig, `parent_mtp` for `time_below_mtp`, or `expected_nbits` for `nbits_retarget_not_applied`.
Omit unknown optional fields.
The related [mining-pools](https://github.com/bitcoin-data/mining-pools) dataset may help identify a coinbase tag.

Include all available `observations`, with a source and provenance URL for each.
Distinct child-chain blocks and independent observers remain separate observations.
Use `merge_mining` for child-chain commitments, `p2p` for direct Bitcoin network reception, and `scrape` for website or API archives where direct reception is not established.
Prefer immutable evidence URLs.

For header rules, observations and full block files are optional.
Body failures require a complete `.bin` that demonstrates the named failure.
For sigops, CI fetches the referenced previous transactions from public APIs, verifies their transaction IDs, and calculates the cost using their output scripts.
For an omitted unconfirmed parent, CI authenticates the parent transaction and a public API's current canonical confirmation height in a different block at or after the candidate height.
The [schema](docs/schema.md#evidence-enforced-by-ci) specifies each rule's evidence contract; observation labels cannot substitute for these checks.

Replaying a `.bin` with `bitcoin-cli submitblock` reproduces context-free failures such as 74638's `bad-txns-vout-toolarge`; connect-level failures such as `bad-blk-sigops` need the historical chain context.
See [`docs/notes.md`](docs/notes.md).

## CI

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python ci/sanity-check.py --fetch-prevouts
python -m unittest discover -s ci -p 'test_*.py'
```

The validator uses `python-bitcoinlib` for Bitcoin parsing and serialization, with the version pinned in `requirements.txt`.
It checks JSONL structure, types, ordering, uniqueness, header hash, PoW and decoded header fields.
It enforces the rule/reject-string mapping, required context and rule-specific predicates.
Validation reports the first error in each record, with its file and line number, then continues to the next record.
Available block files must parse completely and match their transaction merkle roots and applicable witness commitments.
CI checks output-value overflow, forward transaction spends, omitted unconfirmed parents and excessive sigop cost directly.

Sigops checks use a verified cache in `.cache/prevouts/`, restored between GitHub Actions runs.
Missing previous transactions, and omitted-parent confirmation status files, are fetched from public Esplora-compatible APIs when `--fetch-prevouts` is supplied.
API failures, missing evidence and corrupt cached transactions fail validation.
After filling the cache, omit the flag for an offline run; `--prevouts-dir` selects another cache and `--api-url` selects an API base.
The [schema](docs/schema.md#sigops-evidence-and-public-apis) explains the authentication and counting checks.

These checks establish the named failures; they do not execute scripts, authenticate all supplied chain context or replay every consensus check against historical chain state.

A daily run on the default branch revalidates the evidence and keeps the cache warm.
Only successful non-PR runs on the default branch save cache updates; pull requests restore the cache without uploading archives.
Cache retention is not guaranteed; the [schema](docs/schema.md#sigops-evidence-and-public-apis) covers eviction, scheduled-workflow inactivity and recovery.

## License

- **Code** is licensed under the MIT License.
  See `LICENSE`.
- **Data** (in `data/invalid-blocks.jsonl` and `blocks/*`) is dedicated to the public domain under CC0 1.0.
  See `LICENSE-DATA`.
