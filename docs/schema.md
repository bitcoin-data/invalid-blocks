# Schema

`data/invalid-blocks.jsonl` contains one JSON object per line.
The key is the hash of the 80-byte Bitcoin header.
Height is `prev + 1`, not a unique key: different blocks at the same height remain separate records.

A block may enter the dataset only if its header meets its encoded PoW target and its named consensus failure has the evidence required below.
Header/context rules use the supplied header and context.
Body rules require a complete block; sigops additionally requires previous transactions fetched from public APIs or verified cache entries.
Omitted-parent admission additionally requires an authenticated parent transaction, a canonical parent, and that parent's current canonical confirmation height.
Observations document acquisition and incident history, but an explorer label or reported reject string cannot substitute for the evidence check.

JSONL keeps each block's identity, optional context and repeated observations together.
Use UTF-8, LF line endings, a final newline and records sorted by `(height, hash)`.
Numeric fields are JSON integers.
Omit unknown or inapplicable optional fields instead of writing `null` or empty strings.
`context` and `observations` may be omitted when absent.

Hex strings are lowercase without `0x`.
Bitcoin hashes use RPC/display byte order with leading zeros; `header` is the 160-character wire serialization.
Full blocks, when available, are `blocks/{height}-{hash}.bin`.

## Required fields

| Field | Type | Meaning |
| --- | --- | --- |
| `height` | integer | Positive Bitcoin height, derived from the parent, at most 2147483647. |
| `hash` | string | Header hash, the unique key. |
| `header` | string | 80-byte Bitcoin header in hex. |
| `prev_hash` | string | Parent hash, decoded from `header`. |
| `nTime` | integer | Header timestamp in Unix seconds, decoded from `header`. |
| `core_reject_reason` | string | Core reject-string family, required to match the rule table below. Core may format `bad-version(0x...)`; store `bad-version`. |
| `rule` | string | One of the registered consensus rules below, preserving distinctions such as BIP34 rollout stages. Unknown rules are rejected. |

## Optional `context` object

These fields describe the Bitcoin block, not an individual sighting.
A field required by the named rule must be present.
Context is shared across observations; adding another witness does not duplicate the block's validation context.

| Field | Type | Meaning |
| --- | --- | --- |
| `expected_nbits` | string | Canonical compact target as eight hex characters. Required for `nbits_retarget_not_applied`. |
| `parent_mtp` | integer | Parent median-time-past, the median of eleven block timestamps, in Unix seconds. Required for `time_below_mtp`. |
| `coinbase_height` | integer | Height decoded from the BIP34 scriptSig prefix. Required for a BIP34 height mismatch. |
| `coinbase_scriptsig_hex` | string | Coinbase input scriptSig. Required for BIP34 failures and `coinbase_scriptsig_length_above_100`. |
| `pool` | string | Pool identified from the coinbase tag, when known. |
| `parent_kind` | string | `canonical`, `stale`, or `invalid`. `invalid` means the parent is in this dataset. Required as `canonical` for `missing_unconfirmed_parent`. |

## Optional `observations` array

Each object records one witness or acquisition source.
Preserve multiple child blocks on the same chain and independent observers of the same Bitcoin block.
Exact duplicate objects are rejected; repeated child-chain names are allowed.

Observation counts count provenance records, not necessarily independent physical sightings.
An archive may identify a witnessing chain without a child height or hash; retain that evidence without inventing a child identity.
Such a record may overlap a more detailed witness published by another source.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `channel` | string | yes | `merge_mining`, `p2p`, or `scrape`, as defined below. |
| `source` | string | yes | Recorder, provider or evidence catalogue, such as `b10c`, `merge-mining-research`, or `blockchain.com`. |
| `provenance` | string | yes | HTTP(S) URL a reviewer can open to inspect the evidence. Prefer a commit-pinned archive. |
| `child_chain` | string | for `merge_mining` | Merge-mined child chain. |
| `child_height` | integer | no | Height of the witnessing child block. |
| `child_block_hash` | string | no | Child block hash in that chain's native RPC/display order, 64 hex characters. |
| `child_block_time` | integer | no | Timestamp encoded in the child block, in Unix seconds. This is not a receipt time. |
| `child_header` | string | no | 80-byte serialized child header in hex, only for chains with that header format. |
| `first_seen` | integer | no | Actual observation/receipt time in Unix seconds, when recorded. Do not substitute a Bitcoin or child block timestamp. |

`merge_mining` means the Bitcoin parent is evidenced by a child-chain commitment.
This includes AuxPoW and other merge-mining mechanisms, such as RSK; acquiring that evidence from an archive does not change its channel.
`p2p` means a recorder received the Bitcoin block/header from the Bitcoin network, including compact-block relay.
`scrape` means recovery from a website, HTTP API or their archived output, where direct Bitcoin P2P reception is not established.
It describes acquisition, not validity or mining origin.
`source` identifies who supplied the record; `provenance` identifies the inspectable evidence.

Child-chain fields are only allowed for `merge_mining`.
Child header hashing and timestamp layouts differ across chains; the generic validator checks the header's byte encoding, not its chain-specific semantics.

## Rules and Core reject strings

Core functions live in [bitcoin/bitcoin](https://github.com/bitcoin/bitcoin):

- [src/validation.cpp](https://github.com/bitcoin/bitcoin/blob/master/src/validation.cpp): `ConnectBlock`, `ContextualCheckBlock`, `ContextualCheckBlockHeader`
- [src/consensus/tx_check.cpp](https://github.com/bitcoin/bitcoin/blob/master/src/consensus/tx_check.cpp): `CheckTransaction`
- [src/consensus/tx_verify.cpp](https://github.com/bitcoin/bitcoin/blob/master/src/consensus/tx_verify.cpp): `CheckTxInputs`, `GetTransactionSigOpCost`
- [src/script/script.cpp](https://github.com/bitcoin/bitcoin/blob/master/src/script/script.cpp): `GetSigOpCount`
- [src/script/interpreter.cpp](https://github.com/bitcoin/bitcoin/blob/master/src/script/interpreter.cpp): `CountWitnessSigOps`

| `rule` | `core_reject_reason` | Typical Core check |
| --- | --- | --- |
| `bad-txns-vout-toolarge` | `bad-txns-vout-toolarge` | `CheckTransaction` |
| `bad-blk-sigops` | `bad-blk-sigops` | `ConnectBlock` |
| `bad-txns-inputs-missingorspent` | `bad-txns-inputs-missingorspent` | `ConnectBlock` via `CheckTxInputs` |
| `missing_unconfirmed_parent` | `bad-txns-inputs-missingorspent` | `ConnectBlock` via `CheckTxInputs` |
| `bip34_v2_coinbase_height_mismatch` | `bad-cb-height` | `ContextualCheckBlock` |
| `bip34_coinbase_height_mismatch` | `bad-cb-height` | `ContextualCheckBlock` |
| `bip34_coinbase_height_missing` | `bad-cb-height` | `ContextualCheckBlock` |
| `bip66_block_version_below_3` | `bad-version` | `ContextualCheckBlockHeader` |
| `bip65_block_version_below_4` | `bad-version` | `ContextualCheckBlockHeader` |
| `coinbase_scriptsig_length_above_100` | `bad-cb-length` | `CheckTransaction` |
| `time_below_mtp` | `time-too-old` | `ContextualCheckBlockHeader` |
| `nbits_retarget_not_applied` | `bad-diffbits` | `ContextualCheckBlockHeader` |

When multiple version rules apply, use the most recently activated rule.

`bad-cb-height` covers both BIP34 stages.
Records at or above full activation, height 227931, use `bip34_coinbase_height_mismatch` or `bip34_coinbase_height_missing`; current Core still enforces that rule.
Earlier `bip34_v2_coinbase_height_mismatch` records belong to the rollout phase, when the rule applied only to version-2 blocks.
Their rejection must be checked under the rules nodes of that era ran.

## Evidence enforced by CI

`ci/sanity-check.py` registers each rule's reject string, required context and evidence path in `RULES`.
A new rule requires a registry entry, an evidence check, regression tests and an update to this document.
A provenance URL cannot bypass these requirements.

| Rule | Required evidence and check |
| --- | --- |
| `bad-txns-vout-toolarge` | A complete block whose transactions contain an output above 21000000 BTC. |
| `bad-txns-inputs-missingorspent` | A complete block containing a spend of an existing output of a later transaction in that block. Other missing/spent-input cases require an additional evidence checker before admission. |
| `missing_unconfirmed_parent` | A complete block whose parent is canonical (`parent_kind`), whose coinbase height matches the record height, containing a spend of an output whose parent transaction is not in the block, is authenticated by txid, includes the spent output index, and is currently confirmed on the canonical chain at height greater than or equal to the candidate in a different block hash. An unconfirmed or absent API status is not evidence; a parent currently confirmed below the candidate height is a normal spend. |
| `bad-blk-sigops` | A complete block at mainnet height 481824 or later, authenticated previous transactions for every external input, and calculated BIP16/BIP141 sigop cost above 80000. |
| `bip34_v2_coinbase_height_mismatch` | Both coinbase context fields, decoded height matching the scriptSig, and a scriptSig that lacks the exact expected BIP34 prefix. Header version must be at least 2 and height below 227931. Applicability of the historical rolling-version threshold still requires review. |
| `bip34_coinbase_height_mismatch` | Both coinbase context fields, decoded height matching the scriptSig, and a scriptSig that lacks the exact expected BIP34 prefix, at height 227931 or later. A non-minimal encoding of the right number also fails the prefix check. |
| `bip34_coinbase_height_missing` | Coinbase scriptSig with no decodable height prefix, at height 227931 or later. |
| `bip66_block_version_below_3` | Signed header version below 3 at height 363725 or later. |
| `bip65_block_version_below_4` | Signed header version below 4 at height 388381 or later. |
| `coinbase_scriptsig_length_above_100` | Supplied coinbase scriptSig exceeds 100 bytes. |
| `time_below_mtp` | Header timestamp is less than or equal to supplied `parent_mtp`. Equality also fails Core's check. |
| `nbits_retarget_not_applied` | Height is a multiple of 2016; supplied `expected_nbits` encodes a valid target and differs from the header's nBits. |

Every available `.bin` must parse completely, match the dataset header and reproduce its transaction merkle root.
Parsing uses `python-bitcoinlib` without its general consensus-validity checks, because these blocks deliberately violate consensus. Reserializing must reproduce the input bytes exactly; normalized encodings and trailing data are rejected.
Duplicate transaction IDs are rejected by this evidence reader.
Where context includes a coinbase scriptSig, it must match the body's coinbase.
At or after mainnet SegWit activation, witness data must match the coinbase witness commitment.
`ci/block_evidence.py` uses the library's block methods to locate the witness commitment and calculate merkle roots, then applies the evidence checks and overflow/forward-spend predicates.
Omitted-parent admission additionally authenticates the parent transaction and a provider's current canonical confirmation height, as described below.

The validator also checks JSONL structure, field types, decoded Bitcoin header identity, compact target and PoW, ordering, uniqueness and observation fields.
Locally checked predicates establish consistency with the supplied context.
Review must still establish the block height, parent MTP, expected difficulty, historical activation state and the connection between an extracted coinbase script and its header when no complete body is available.
The retarget check does not reconstruct the previous difficulty or prove that it was reused.
CI does not execute scripts, validate child-chain commitments, reconstruct historical chain state or fetch observation provenance.
It verifies the named failures, not every consensus rule or the exact first rejection a historical node would return.
Incident background is in [notes.md](notes.md).

## Sigops evidence and public APIs

`ci/prevouts.py` resolves the previous transactions named by inputs in a sigops block.
Transactions earlier in the same block are resolved from the body.
External transactions are fetched as raw hex from Esplora-compatible endpoints, defaulting to `mempool.space/api` with `blockstream.info/api` as a fallback.
Use `--fetch-prevouts` to permit downloads and repeat `--api-url` to supply other endpoint bases.
Requests have timeouts, bounded retries and two download workers.
Download failures fail validation explicitly.

Before accepting a response, CI parses the whole transaction and verifies its txid against the spending input.
It caches stripped transaction bytes under `.cache/prevouts/{txid}.bin`; witness data from previous transactions is unnecessary for authenticating their outputs.
Every cache hit undergoes the same identity check.
A corrupt cache entry fails instead of being trusted or silently replaced.
GitHub Actions caches previous transactions by the contents of the block files, reusing older caches to reduce downloads when blocks are added.
Only successful non-PR runs on the default branch save an archive, and only when no exact cache exists for that block set.
Without `--fetch-prevouts`, all required entries must already be cached; missing evidence is an error.
Cache contents are not committed to the dataset.

The workflow also runs daily at 03:23 UTC on the default branch, revalidating the cached evidence and fetching any missing entries.
Pull requests can restore this default-branch cache, but do not upload cache archives, avoiding separate archives for unmerged changes.
The schedule takes effect once the workflow is merged into the default branch; `workflow_dispatch` allows a manual run.
This keeps the cache in use, but does not make it permanent storage: GitHub's default policy [evicts caches unused for seven days and can evict entries under storage pressure](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#usage-limits-and-eviction-policy).
GitHub also [disables public-repository schedules after 60 days without repository activity](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).
Maintainers must re-enable a disabled schedule; after eviction, validation downloads and authenticates the missing transactions again.
Long-term preservation independent of public API availability requires a separate durable archive.

Script classification and push decoding use `python-bitcoinlib`. A small local sigop counter remains because version 0.12.2's accurate-multisig helper raises an exception and its malformed-push handling does not preserve the accumulated count.
`ci/block_evidence.py` calculates the legacy cost (input and output sigops multiplied by four), additional P2SH cost (accurate redeem-script count multiplied by four), and version-0 witness cost.
Taproot has a separate validation budget and adds no cost to this block-wide counter.
The block's witness commitment is verified before counting witness scripts, so changing a witness script cannot manufacture evidence while leaving the transaction merkle root unchanged.

The previous-transaction txid authenticates its output scripts, but does not prove that an output was unspent at the candidate's parent or that a signature is valid.
Those are separate consensus checks.
This calculation establishes the excessive sigop cost without reconstructing a historical UTXO set or depending on an explorer's rejection label.

## Omitted-parent evidence and canonical confirmation

`missing_unconfirmed_parent` shares Core reject string `bad-txns-inputs-missingorspent` with the in-block forward-spend rule, but the two rules prove different facts.
The forward-spend checker does not look up external inputs. Treating every missing prevout as a failure would reject most valid blocks, which spend previously confirmed outputs.

Admission requires a complete body, `parent_kind` `canonical`, a coinbase height matching the record height, a non-coinbase input whose prevout txid is not among the block's transaction IDs, an authenticated parent transaction that includes that output index, and a current canonical confirmation of that parent at height greater than or equal to the candidate in a different block hash.
The 1Hash 474294 case is confirmation in the canonical same-height competitor.
A parent currently confirmed later than the candidate is also admitted.
A parent currently confirmed earlier is not this failure.

`ci/prevouts.py` reuses the previous-transaction cache for the omitted parent and stores `{txid}.status.json` for the height and block hash returned by Esplora `/tx/{txid}/status`.
That endpoint reports the parent's confirmation on the chain the provider currently calls canonical, not a historical first-seen height.
The files are verified only as well-formed integers and 64-character hashes, not as a reconstructed UTXO set.
`--fetch-prevouts` permits both the raw-transaction download and the status download.
Unconfirmed responses are not cached and cannot satisfy the rule.
A 404 or other endpoint gap is not treated as proof that the output was missing at connect time.
Offline validation succeeds once the omitted parent transaction and its confirmation status are in `.cache/prevouts/`.

A parent that confirmed below the candidate, was reorged out, and later re-confirmed at or after the candidate height would pass this check even though the output existed on the chain the miner was extending.
Duplicate historical transaction IDs can also make the confirmation height ambiguous.
The checker does not prove that the output was unspent at the candidate's parent tip; it proves that the named parent is not currently confirmed in the canonical chain before that height.
