# Notes

Background that does not fit the [schema](schema.md) or the README.
Deeper per-block history is documented elsewhere; see the observation `provenance` URLs in [`data/invalid-blocks.jsonl`](../data/invalid-blocks.jsonl).

## Replaying full blocks

A `.bin` can be replayed with `bitcoin-cli submitblock`, but what comes back depends on where the violation is caught.
74638 fails the context-free `CheckBlock` checks before anything is stored, so every replay returns `bad-txns-vout-toolarge` on any node.
Its header alone is valid, though: `submitheader` accepts it (noted in <https://github.com/bitcoin-data/stale-blocks/pull/65>), since every header-level check passes and the violation lives entirely in the body.
The transaction-ordering blocks, 474294, the two coinbase overpayments and the 2023 sigops blocks fail in `ConnectBlock`, which a node only runs when it is about to extend its active chain with the block.
Replayed today they sit on a branch with less work than the tip, so a node seeing them fresh stores them after the context-free checks and never connects them: `submitblock` returns `inconclusive` and the block becomes a `valid-headers` chain tip.
A node that already stores them returns `duplicate`, and only a node that attempted the connect at their original tip and marked them failed returns `duplicate-invalid` (the result reported in <https://github.com/bitcoin-data/stale-blocks/pull/11>).

## Pool attributions by address or report

Most `pool` values are coinbase tags.
The records below attribute a pool another way, and `pool_basis` says which.
363731 (BTC Nuggets) and 363967 (Bitsolo) are attributed by payout address: 363967's coinbase, recovered from the AuxPoW record in Namecoin block 237930, carries no tag and pays `18zRehBcA2YkYvsC7dfQiFJNyjmWvXsvon`, the address [mining-pools](https://github.com/bitcoin-data/mining-pools/blob/af720b67faa2f157264db33c644eb1b0fa95af5f/pools/bitsolo.json) lists for Bitsolo; 363731 is covered in its own note below.
The coinbases of 363967, 946213 and 957780 are authenticated by coinbase proofs built from the AuxPoW records in Namecoin blocks 237930, 821553 and 833329, so the Bitsolo address and the `/F2Pool/` tags are read from transactions the headers commit to.
The two F2Pool coinbases pay 3.20191623 and 3.14128765 BTC in total, the 3.125 BTC subsidy plus fees, with all but a 546-satoshi output going to `1AfCc4F9c4VTYSE31PUe2kUEKs6ZxiDjxm`.
474294 and 477115 are attributed to 1Hash from the [BitcoinTalk thread](https://bitcointalk.org/index.php?topic=2041607.0) of July 2017 that discussed both blocks; their coinbases carry `/NYA/` and no pool tag.
226845, 226895 and 226912 are attributed to mmpool, Chris Double's [Bitparking merged-mining pool](https://bitcointalk.org/index.php?topic=57148.0) at mmpool.bitparking.com, from his [20 March 2013 post](https://bitcointalk.org/index.php?topic=57148.msg1646921#msg1646921) in that thread reporting three invalidated blocks that day, and the [bitcoin-dev log](https://buildingbitcoin.org/bitcoin-dev/log-2013-03-20.html) of the same day, where he reports the `block height mismatch in coinbase` rejection and names 226845; the later 367047 carries the `mmpool` tag itself.

## Coinbase proofs

Merge-mined recoveries supply a header and a coinbase extract, and until the coinbase is placed in the block that extract is only a claim.
Every record with `coinbase_scriptsig_hex` and no body now has a proof file except 649674: 38 coinbases with their merkle branches from the AuxPoW records in Namecoin blocks, read from a Namecoin Core node by the child block hash in each record's `merge_mining` observation, and 363731's coinbase reserialised from the pinned blockchain.info dump and placed by that dump's 99 txids.
CI reproduces each header's merkle root from the proof and requires the context scriptSig to equal the proved one, so every `tag` and `address` attribution in the dataset is read from bytes the header commits to.

649674 has no Bitcoin merkle path.
Its Hathor block carries the coinbase and a twelve-hash path, and that path reproduces the header's merkle root only when the leaf is the double-SHA256 of the coinbase's full witness serialisation rather than its txid: the pool's merged-mining code built the tree from the wrong hash.
A Bitcoin node given the block would fail `CheckBlock` with `bad-txnmrklroot` before reaching the coinbase-height check the dataset records.
The scriptSig therefore stays an extract, bound to the header only through that non-standard tree, and the second failure is not registered as a rule.

## Reported blocks

`data/reported-blocks.jsonl` keeps ten blocks that were reported as invalid but cannot be admitted.
Three Eligius blocks of September 2012 were reported as coinbase overpayments alongside the admitted 197438; 197883's header survives but its other transaction and fee do not, and 197701 and 197705 are known only by hash.
P2Pool's 212048 is known from a December 2012 `InvalidChainFound` report and a node-history dump, without a header or a rejection reason.
Five version-2 hashes from the BIP66 and BIP65 windows come from a March 2017 bitcoin-dev message and still lack headers.
Bitcoin Unlimited's 450529 has a recovered header and a reported size of 1000023 bytes, but no body bytes to establish it.
Issues labelled [`reported`](https://github.com/bitcoin-data/invalid-blocks/issues?q=label%3Areported) record what has already been searched for each incident and are the place to bring the missing header or body.

## Incident notes

### 507514, 509557, 515319 and 534339 - AntPool parent-transaction reuse (2018)

Each block includes non-coinbase transactions already confirmed in the canonical parent named by its header, so their inputs were already spent and `ConnectBlock` fails with `bad-txns-inputs-missingorspent`.
The dataset calls this `already_confirmed_in_parent`, separately from forward spends and missing unconfirmed parent transactions.

| Height | Date (UTC) | Transactions reused from parent | Non-coinbase transactions in candidate |
| --- | --- | ---: | ---: |
| [507514](../blocks/507514-000000000000000000571c2b98a090c15774cb7400bd4a50b160d488d14055d0.bin) | 2018-02-04 | 338 | 737 |
| [509557](../blocks/509557-00000000000000000027894f0969c2f79a6cdb1231750e048effbd17c88da431.bin) | 2018-02-17 | 1253 | 2322 |
| [515319](../blocks/515319-00000000000000000014c1ee89b61a84e3e30dd9b2c78c9916d323a2775bc613.bin) | 2018-03-27 | 79 | 79 |
| [534339](../blocks/534339-0000000000000000001a04286794b25ff10dfdb1bb601b17280dfc1ef933a0ba.bin) | 2018-07-30 | 218 | 218 |

Block 515319 copies all 79 of its parent's non-coinbase transactions in order; 534339 carries a subset in a different order; the other two mix parent transactions with new ones.
All four coinbases carry `Mined by AntPool`, and the pattern suggests a template whose previous-block hash was refreshed while its transaction list was kept, an inference from the bodies rather than a recovered record.
It is the sibling of the 584802 incident, where the transactions were dropped while their fees stayed in the coinbase.

The pinned chainquery archive holds the hashes in [orphans_chainquery.com.json](https://github.com/NStifter/mergedmonitor/blob/54344d4e355f73eb94bef8d391e8fb6e4a9323a6/fork-analysis/chainquery.com/orphans_chainquery.com.json) and labels their tips `invalid` in [tips.json](https://github.com/NStifter/mergedmonitor/blob/54344d4e355f73eb94bef8d391e8fb6e4a9323a6/fork-analysis/chainquery.com/tips.json); only the former is recorded as a scrape observation, the labels are background.
The bodies reached stale-blocks in [abee96d](https://github.com/bitcoin-data/stale-blocks/commit/abee96d) without a recorded acquisition path, so they establish no further observation.

CI checks the named txid in each body against the parent's ordered txid list, authenticated by the parent header's merkle root, with a canonical-height lookup binding the parent to height minus one.
All four pass python-bitcoinlib's context-free `CheckBlock` and witness-commitment checks, which do not replay historical `ConnectBlock`.
On 17 September 2026 a Bitcoin Core v31.1.0 node, rewound to each parent with `invalidateblock`, returned `bad-txns-inputs-missingorspent` from `submitblock` for all four.
Core skips BIP30 checks below height 1983702 on the known mainnet chain after BIP34 (see the [guard in validation.cpp](https://github.com/bitcoin/bitcoin/blob/bf8402c8803f085a50df96cb7956033cd252e9ab/src/validation.cpp#L2412)), so the named failure is the reuse of spent inputs, not `bad-txns-BIP30`.

A 2026-09-15 sweep of all 1086 stale-blocks bodies at [be1e859](https://github.com/bitcoin-data/stale-blocks/commit/be1e8597615c3372aab9ca437a9cd554822b6870) found no other non-coinbase intersection among the 1073 bodies extending canonical parents; 13 extended noncanonical parents and were out of scope.
A negative result does not establish that a block satisfies every consensus rule.

### 173928, 173957, 173998 and 174605 - P2SH redeem-script failure (2012)

Each body includes the same 123-byte transaction, `4005d6bea3a93fb72f006d23e2685b85069d270cb57d15f0c057ef2d5e3f78d2`, which spends a pay-to-script-hash output funded at canonical 170054, `b0539a45de13b3e0403909b8bd1a555b8cbe45fd4e3f3fda76f3a5f52835c29d:1`, worth 400000 satoshis.
Its scriptSig pushes only the redeem script, a 1-of-1 `OP_CHECKMULTISIG`.
The pre-BIP16 template check hashes that push and compares it, and passes; executing the redeem script finds no signature on the stack and fails.
Nodes applying the 1 April 2012 rules rejected these blocks while older nodes accepted them, which is how the same transaction was included by many miners for months.
Core today reports `block-script-verify-flag-failed (Operation not valid with the current stack size)`.

The four bodies are reconstructions: the coinbase from Namecoin's AuxPoW record, the other transactions from their later confirmations on the accepted chain, and the invalid spend itself; each reproduces its header's merkle root.
They hold 67, 64, 23 and 14 transactions in 31258, 41258, 7662 and 4855 bytes.
CI fetches the funding transaction, checks its txid, and evaluates the named input with and without P2SH.
The blockchain.info block pages archived by the Wayback Machine in April 2012 list each block's transactions.
The [2 April 2012 bitcoin-dev log](https://buildingbitcoin.org/bitcoin-dev/log-2012-04-02.html) records the first `P2SH VerifySignature failed` rejections and the [4 April log](https://buildingbitcoin.org/bitcoin-dev/log-2012-04-04.html) preserves the transaction.
The other 85 blocks carry the same spend without a complete body and are admitted from proof files holding the transaction and each block's ordered txids.
Among them are 173886, whose rejection a node log in the [7 April 2012 log](https://buildingbitcoin.org/bitcoin-dev/log-2012-04-07.html) records at 14:58:54 UTC on 1 April; 174772, which the [28 November 2012 log](https://buildingbitcoin.org/bitcoin-dev/log-2012-11-28.html) shows an unpatched node connecting as its best chain; and 189498, the last, reported in the [17 July 2012 log](https://buildingbitcoin.org/bitcoin-dev/log-2012-07-17.html).
The txid lists come from the Wayback Machine's 2012 captures of the blockchain.info block pages (42 blocks) or from the Decker and Wattenhofer orphan archive preserved in mergedmonitor (43); the [June 2012 capture](https://web.archive.org/web/20120615080519id_/http://blockchain.info:80/tx-index/3618498/4005d6bea3a93fb72f006d23e2685b85069d270cb57d15f0c057ef2d5e3f78d2) of the transaction's own page lists 88 of the 89 blocks.

### 74638 - value overflow (2010)

`bad-txns-vout-toolarge` is the 2010 overflow incident ([CVE-2010-5139](https://en.bitcoin.it/wiki/Value_overflow_incident)).
A transaction created two outputs of about 92 billion BTC each.
The outputs were non-negative, so they passed the then-current per-output check; their sum overflowed a signed 64-bit integer and wrapped negative, so the input-versus-output check passed too.
Bitcoin 0.3.10 added a money-range check (there is no BIP).
Core now rejects any single output above `MAX_MONEY` with this string.

### 783426 and 784121 - F2Pool sigops (2023)

Both blocks exceed the sigop limit: accurate sigop cost 80003 against the 80000 maximum, counted in `ConnectBlock`.
The context-free legacy count is under the limit, which is why replay cannot reproduce `bad-blk-sigops`.
Documented in [b10c observation 11](https://b10c.me/observations/11-invalid-blocks-783426-and-784121/).
CI reproduces the 80003 cost for each block using previous transactions authenticated against its inputs.
Block 783426 has 74520 legacy cost, 160 P2SH cost and 5323 witness cost; block 784121 has 72204, 908 and 6891 respectively.
The previous transactions are fetched from public APIs and cached; the block bodies and their witness commitments supply the remaining evidence.

### 477115 - transaction ordering (2017)

Three of the block's 255 transactions spend an output of a transaction that appears later in the same block.
As with 809478, `ConnectBlock` encounters these inputs before their outputs are available: `bad-txns-inputs-missingorspent`.
This violation is re-derivable from the [preserved full block](../blocks/477115-0000000000000000013ee4a86822d37a061732e04ee5f41fb77168f193363d1b.bin).

### 809478 - MARA transaction ordering (2023)

145 of the block's 2528 transactions spend an output of a transaction that appears later in the same block.
`ConnectBlock` processes transactions in order, so the first such input fails the coins lookup: `bad-txns-inputs-missingorspent`.
This violation is re-derivable from the `.bin` alone.
Documented in [b10c observation 07](https://b10c.me/observations/07-invalid-block-809478/).

### 474294 - missing parent transaction (2017)

Transaction 110 spends `b11a78c6c61af1cb37586f639050d74b95c2b0fd525623b6cb6a4bb4fba46a0e:1`, and that parent transaction is not in the block.
The record names that outpoint in `missing_prevout`.
Both transactions confirmed in the competing block at the same height, `000000000000000000db2504327e272fe7658fac0dd0741f46b212256e500886`.
The spent output did not exist at the tip of the previous block, so `ConnectBlock` fails with `bad-txns-inputs-missingorspent`.
This is not an in-block ordering error: unlike 477115 and 809478, no later transaction in the body creates the output.
The [preserved full block](../blocks/474294-00000000000000000182acdf5657c93a0769dc6f9004047496b2e15efc6a4232.bin), the parent transaction, its current confirmation and the canonical block hash at 474293 re-derive the violation.

Contemporaneous discussion is [BitcoinTalk topic 2041607](https://bitcointalk.org/index.php?topic=2041607.0).

### 363731 - BIP66 fork trigger (2015)

This version-2 header started the 4 July 2015 fork.
The fixture's other five headers, all version 3, chain from it through 363736; the network reorganized away from them, and the [alert](https://bitcoin.org/en/alert/2015-07-04-spv-mining) that followed asked lightweight-wallet users to wait for extra confirmations.
Those five descendants fail by ancestry and are not recorded here.
The 80-byte header comes from a blockchain.info block dump preserved in the [BTC Relay test fixture for this fork](https://github.com/crossclaim/btcrelay-sol/tree/1cf676d387c4514770b91e4ca15094194f446677/test/testdata/old_headers/fork/20150704), which also holds the six raw headers; the same field set survives independently in a [TypeScript port of BTC Relay](https://github.com/adambor/BtcRelay-EVM-TS/blob/8a8c10908655f356f5985d3d8af951f756a92ada/src/test/forkedBlocks.ts).
The [dump's](https://github.com/crossclaim/btcrelay-sol/blob/1cf676d387c4514770b91e4ca15094194f446677/test/testdata/old_headers/fork/20150704/363731.json) 99 transaction IDs reproduce the header's merkle root, and its coinbase fields reserialize to the first of those IDs, which ties the recorded scriptSig to the header.
The coinbase pays `1BwZeHJo7b7M2op7VDfYnsmcpXsUYEcVHm`, the address [mining-pools](https://github.com/bitcoin-data/mining-pools/blob/af720b67faa2f157264db33c644eb1b0fa95af5f/pools/btc-nuggets.json) lists for BTC Nuggets; the scriptSig tags are `/P2SH/` and `/stratumPool/`, so the attribution is by address.
A [16 March 2017 bitcoin-dev message](https://gnusha.org/pi/bitcoindev/48d3940ab1a2bd53c6e056ce7fbcd361@cock.lu/) lists this hash among blocks a node rejected with `bad-version(0x00000002)`.
### 584802 - AntPool coinbase overpayment (2019)

The [334-byte body](../blocks/584802-0000000000000000000b47042b90c6a893e6e5cdef70c92beefb88f4c5fa5a69.bin) contains only its coinbase, which pays 1326546691 satoshis against the 1250000000 subsidy: no fees, an excess of 76546691 satoshis (0.76546691 BTC).
Header hash, proof of work, merkle root, witness commitment and BIP34 height reproduce from the body, and the coinbase carries `Mined by AntPool112`.
Its parent is canonical 584801, `0000000000000000001b253b1fac766189e15d7f7078191002e5427ac7b8f9f1`, which CI checks against the API's block hash at that height.

The header entered stale-blocks through the [2022 chainquery.com getchaintips import](https://github.com/bitcoin-data/stale-blocks/commit/97db32bf74481da71d0893ac37152a3a9479424d) and the body in commit `abee96d`, which does not record how it was acquired, so no P2P observation is claimed.
The binary is preserved byte-for-byte, SHA-256 `08e6fb81bc61db0836d8726f9fe40459fc5a9d3f3e64621a74b21b2af4d48df0`.
Elastos child 419444 independently witnesses the parent; its Research provenance records acquisition, not the verdict.

### 197438 - Eligius coinbase overpayment (2012)

The [1890-byte body](../blocks/197438-0000000000000307872ec2eb0eae2dca3ed9ce6af9e024412cb3ddfe8afd12a7.bin) holds the coinbase and one ordinary transaction.
The coinbase pays 5001000000 satoshis against the 5000000000 subsidy.
The transaction spends output 1 of `8d86cbc5bf98ae815b9f360d0ac208109a75ab7988fc33276fe1eebe46e7fd8f`, confirmed at canonical 197333 and worth 503413843 satoshis, and pays out exactly that, so the fee is zero and the excess is 1000000 satoshis (0.01 BTC).
The block predates BIP34, so the height is bound by the canonical parent at 197437, `000000000000008cb385de6a68aaaaa6c3974b98cf8f544b8cce0db1855256c4`, not by a coinbase prefix; the coinbase tag is `Eligius`.

The body is a reconstruction: header and coinbase from the AuxPoW record in Ixcoin block 91289, the transaction from its later confirmation at canonical 197523; the two txids reproduce the merkle root.
Luke-Jr's node log, pasted to bitcoin-dev IRC on [9 September 2012](https://buildingbitcoin.org/bitcoin-dev/log-2012-09-09.html), reports `InvalidChainFound` for this block and three other Eligius blocks, 197701, 197705 and 197883, whose bodies remain missing.
Core's check then and now is the same subsidy-plus-fees comparison.
stale-blocks [PR #139](https://github.com/bitcoin-data/stale-blocks/pull/139) added the header as a stale block; it is invalid, not stale, and is removed there once this record is published.
