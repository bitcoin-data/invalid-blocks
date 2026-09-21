# Schema

`data/invalid-blocks.jsonl` contains one JSON object per line.
The key is the hash of the 80-byte Bitcoin header.
Height is `prev + 1`, not a unique key: different blocks at the same height remain separate records.

A block may enter the dataset only if its header meets its encoded PoW target and its named consensus failure has the evidence required below.
Header/context rules use the supplied header and context.
Body rules require a complete block; sigops, missing-parent, parent-transaction reuse and fee accounting rules additionally require evidence fetched from public APIs or verified cache entries.
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
| `coinbase_height` | integer | Height decoded from the BIP34 scriptSig prefix. Required for a BIP34 height mismatch and `already_confirmed_in_parent`. |
| `coinbase_scriptsig_hex` | string | Coinbase input scriptSig. Required for BIP34 failures, `coinbase_scriptsig_length_above_100` and `already_confirmed_in_parent`. |
| `pool` | string | Pool the block is attributed to, when known. Requires `pool_basis`. |
| `pool_basis` | string | How the pool was identified: `tag` when the pool name appears as a tag in the coinbase scriptSig, `address` when the coinbase payout address is listed for the pool in [mining-pools](https://github.com/bitcoin-data/mining-pools), or `reported` when only a contemporaneous report names the pool. Required whenever `pool` is present and not allowed otherwise. Descriptive: CI checks the value, not the attribution. |
| `parent_kind` | string | Chain status of the previous block: `canonical`, `stale`, or `invalid`. `invalid` means the previous block is in this dataset. Descriptive, except that the rules which look up the canonical block at the previous height (`missing_unconfirmed_parent`, `already_confirmed_in_parent`) reject any other value. |
| `missing_prevout` | string | Outpoint as `txid:vout`, spent by a non-coinbase input whose transaction is not in the block. Required for `missing_unconfirmed_parent`. |
| `parent_txid` | string | Lowercase 64-hex txid of a non-coinbase transaction in both the candidate and its canonical parent. Required for `already_confirmed_in_parent`. |

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
| `bad-cb-amount` | `bad-cb-amount` | `ConnectBlock` |
| `bad-txns-inputs-missingorspent` | `bad-txns-inputs-missingorspent` | `ConnectBlock` via `CheckTxInputs` |
| `missing_unconfirmed_parent` | `bad-txns-inputs-missingorspent` | `ConnectBlock` via `CheckTxInputs` |
| `already_confirmed_in_parent` | `bad-txns-inputs-missingorspent` | `ConnectBlock` via `CheckTxInputs` |
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
| `bad-cb-amount` | A complete block extending the block a public API reports at the previous height, whose coinbase pays strictly more than the mainnet subsidy at that height plus fees calculated from authenticated previous transactions. A coinbase-only body has no fees to fetch. |
| `bad-txns-inputs-missingorspent` | A complete block containing a spend of an existing output of a later transaction in that block. A spend of a parent transaction absent from the block uses `missing_unconfirmed_parent`. |
| `missing_unconfirmed_parent` | A complete block extending the block a public API reports at the previous height. The `missing_prevout` outpoint must be spent by an input in the block and created by a transaction not in the block, and the API must currently report that transaction confirmed in another block at this height or later. Unconfirmed or absent status is not evidence. |
| `already_confirmed_in_parent` | A complete block whose named `parent_txid` is a non-coinbase transaction in both that body and its canonical parent. The parent's ordered txid list must reproduce the merkle root of a hash-verified parent header, and the canonical hash at height minus one must equal `prev_hash`. Require `parent_kind=canonical` and body-derived coinbase fields with `coinbase_height` equal to the record height. |
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

The validator also checks JSONL structure, field types, decoded Bitcoin header identity, compact target and PoW, ordering, uniqueness and observation fields.
Locally checked predicates establish consistency with the supplied context.
Review must still establish the block height, parent MTP, expected difficulty, historical activation state, chain status where no canonical lookup is required, and the connection between an extracted coinbase script and its header when no complete body is available.
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

## Missing parent transaction evidence

Here "parent transaction" is the transaction that created a spent output, and "previous block" is the block the candidate extends.

`missing_unconfirmed_parent` shares reject string `bad-txns-inputs-missingorspent` with the forward-spend rule, but that checker only sees transactions inside the block.
Most valid blocks spend outputs created in earlier blocks, so a parent transaction absent from the body is not by itself a failure.
The rule requires the parent transaction named by `missing_prevout` to be currently confirmed in another block at the candidate's height or later: it was not in the chain below the candidate, so its output did not exist at the tip of the previous block.
A parent transaction currently confirmed below that height is a normal spend.
That inference holds only if the candidate extends the canonical chain, so CI also requires the block hash the API reports at the previous height to equal `prev_hash`.

`ci/prevouts.py` caches the parent transaction as `{txid}.bin`, the Esplora `/tx/{txid}/status` reply as `{txid}.status.json`, and the `/block-height/{n}` reply as `height-{n}.hash`; `--fetch-prevouts` permits the downloads.
A failed download or an unconfirmed reply fails validation, and an unconfirmed status is never cached.
Offline validation needs all three files in `.cache/prevouts/`.

This check does not reconstruct a UTXO set or replay `ConnectBlock`, and it trusts the configured APIs for the confirmation and the canonical hash.
Cached confirmation and block-hash entries are snapshots from their first fetch; delete them to re-check.

## Transactions already confirmed in the parent

`already_confirmed_in_parent` identifies reuse of a non-coinbase transaction from the canonical previous block.
That transaction has already consumed its inputs, so including it again fails the input availability check.
This differs from an in-block forward spend and from a missing unconfirmed parent transaction; all three share `bad-txns-inputs-missingorspent` as their reject-string family.
The body must contain the named `parent_txid`, and neither the body's coinbase nor the parent's coinbase may serve as the witness.

`ci/prevouts.py` fetches `/block/{prev_hash}/header` as hex and requires exactly 80 decoded bytes whose header hash equals `prev_hash`.
It fetches `/block/{prev_hash}/txids` as a nonempty JSON array of 64-hex transaction IDs, rejecting duplicates.
Converting the ordered IDs from display byte order and rebuilding their merkle tree must reproduce the header's merkle root.
The first list entry is the canonical parent's coinbase and is excluded from membership checks.
This binds the list contents to the parent without downloading its full body.
The `/block-height/{height-1}` response must also equal `prev_hash`; the canonical-chain assertion is trusted API context, not proved by a header alone.

The cache stores the header's hex response as `{prev_hash}.header`, the ordered list as `{prev_hash}.txids.json`, and the canonical hash as `height-{height-1}.hash`.
Header downloads are limited to 256 bytes and txid-list downloads to 2 MiB.
Both cached and downloaded evidence undergo the same checks; missing or corrupt evidence fails validation.
`--fetch-prevouts` permits downloads, and a warmed cache supports offline checks.
The cache retention and scheduled-workflow limits described above also apply to these files.

The checker considers only the immediate canonical parent, not older ancestors or the UTXO set, and does not execute scripts or replay historical `ConnectBlock`.
Canonical hashes remain snapshots from their first fetch, as in the missing-parent check.

## Coinbase amount evidence

`bad-cb-amount` compares the sum of the coinbase outputs with the mainnet subsidy plus the block's fees.
The subsidy starts at 50 BTC and halves every 210000 blocks.
The height sets the subsidy, so the rule binds it as the missing-parent and parent-reuse rules do: the API's block hash at `height - 1`, cached as `height-{height-1}.hash`, must equal `prev_hash`.
No BIP34 prefix is required, which admits blocks from before that rule; a supplied `coinbase_height` must still match the scriptSig.

A coinbase-only block has zero fees and needs no previous transactions.
Otherwise `ci/prevouts.py` loads every external input's transaction and verifies its txid before its output value is used; earlier transactions in the body supply internal outputs, and later ones and the coinbase cannot.
Fees are inputs minus outputs, never an explorer's fee field.
Missing outputs, repeated spends, money-range violations and negative fees are errors, and a coinbase that pays exactly the subsidy plus fees is not admitted.
Authenticated bytes establish output values, not historical unspentness, coinbase maturity or script validity; this is not a `ConnectBlock` replay.
