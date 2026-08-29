# Schema

Identity is the hash of the 80-byte Bitcoin header, and height is just `prev + 1`, not a unique key.
Same-height, different hashes are distinct rows.

A hash may enter the primary table (`data/invalid-blocks.csv`) only if it meets the target encoded in its header, and there is a named consensus failure that can be re-checked from committed bytes or, where the failure needs chain state (`bad-blk-sigops`), is documented by a `provenance` URL in `data/observations.csv`.

```
data/invalid-blocks.csv
data/context.csv
data/observations.csv
```

`header` is 160 hex characters, Bitcoin wire order.
Hashes are 64 hex characters, RPC/display byte order, with leading zeros.
CSVs are LF.
Empty cells mean unknown or not applicable.

---

## `data/invalid-blocks.csv`

```
height,hash,header,prev_hash,nTime,core_reject_reason,rule
```

| Column | Meaning |
| --- | --- |
| `height` | Bitcoin height (`prev + 1`). Not unique. |
| `hash` | Header hash. Primary key. |
| `header` | 80-byte header, hex. |
| `prev_hash` | Parent hash. Decoded from `header`. |
| `nTime` | Header timestamp, unix seconds. Decoded from `header`. |
| `core_reject_reason` | Bitcoin Core reject-string family (`bad-blk-sigops`, `bad-cb-height`, `bad-version`, `time-too-old`, `bad-diffbits`, `bad-cb-length`, `bad-txns-vout-toolarge`, `bad-txns-inputs-missingorspent`). Core may format `bad-version(0x…)`; this column stores the family. |
| `rule` | Named consensus rule. Keeps BIP34 stages distinct when Core only logs `bad-cb-height`. |

Full blocks, when present, are `blocks/{height}-{hash}.bin`.

---

## `data/context.csv`

Optional, except for the cells a given `rule` needs.
At most one row per hash.

```
hash,expected_nbits,parent_mtp,coinbase_height,coinbase_scriptsig_hex,pool,parent_kind
```

| Column | Meaning |
| --- | --- |
| `hash` | Joins to the primary row. |
| `expected_nbits` | Canonical compact target at `height`. Needed for `nbits_retarget_not_applied`. |
| `parent_mtp` | Parent median-time-past (11-block median). Needed for `time_below_mtp`. |
| `coinbase_height` | BIP34 height prefix from the coinbase scriptSig. |
| `coinbase_scriptsig_hex` | Coinbase input scriptSig. |
| `pool` | From the coinbase tag, when known. |
| `parent_kind` | `canonical`, `stale`, or `invalid`. Empty if unclassified. `invalid` means the parent is in this dataset. |

---

## `data/observations.csv`

Optional.
One row per witness (child-chain AuxPoW or Bitcoin P2P).

```
hash,channel,source,child_chain,child_height,first_seen,provenance
```

| Column | Meaning |
| --- | --- |
| `channel` | `auxpow` or `p2p`. Compact-block relay is `p2p`. |
| `source` | Who recorded it (`stale-blocks`, `b10c`, `merge-mining-research`). |
| `child_chain` | Merge-mined child chain. Empty for `p2p`. |
| `child_height` | Height on that child chain, when known. |
| `first_seen` | Unix time of the first observation, when known. |
| `provenance` | A URL a reviewer can open. Merge-mined rows point at the research catalogue. P2P rows point at an external source. |

---

## Rules and Core reject strings

Core functions live in [bitcoin/bitcoin](https://github.com/bitcoin/bitcoin):

- [`src/validation.cpp`](https://github.com/bitcoin/bitcoin/blob/master/src/validation.cpp): `ConnectBlock`, `ContextualCheckBlock`, `ContextualCheckBlockHeader`
- [`src/consensus/tx_check.cpp`](https://github.com/bitcoin/bitcoin/blob/master/src/consensus/tx_check.cpp): `CheckTransaction`
- [`src/consensus/tx_verify.cpp`](https://github.com/bitcoin/bitcoin/blob/master/src/consensus/tx_verify.cpp): `CheckTxInputs`

| `rule` | `core_reject_reason` | Typical Core check |
| --- | --- | --- |
| `bad-txns-vout-toolarge` | `bad-txns-vout-toolarge` | `CheckTransaction` |
| `bad-blk-sigops` | `bad-blk-sigops` | `ConnectBlock` |
| `bad-txns-inputs-missingorspent` | `bad-txns-inputs-missingorspent` | `ConnectBlock` via `CheckTxInputs` |
| `bip34_v2_coinbase_height_mismatch` | `bad-cb-height` | `ContextualCheckBlock` |
| `bip34_coinbase_height_mismatch` | `bad-cb-height` | `ContextualCheckBlock` |
| `bip34_coinbase_height_missing` | `bad-cb-height` | `ContextualCheckBlock` |
| `bip66_block_version_below_3` | `bad-version` | `ContextualCheckBlockHeader` |
| `bip65_block_version_below_4` | `bad-version` | `ContextualCheckBlockHeader` |
| `coinbase_scriptsig_length_above_100` | `bad-cb-length` | `CheckTransaction` |
| `time_below_mtp` | `time-too-old` | `ContextualCheckBlockHeader` |
| `nbits_retarget_not_applied` | `bad-diffbits` | `ContextualCheckBlockHeader` |

`bad-cb-height` covers both BIP34 stages.
`rule` keeps the stage because the two stages verify differently.
Rows at or above full activation (height 227931) use `bip34_coinbase_height_mismatch` / `bip34_coinbase_height_missing`; current Core still enforces the rule there and would emit `bad-cb-height`.
The `bip34_v2_coinbase_height_mismatch` rows sit in the earlier rollout phase, when the rule bound only version-2 blocks; current Core enforces nothing below 227931, so their rejection can only be re-checked under the rules nodes of the era ran.

Incident background for specific blocks is in [notes.md](notes.md).
