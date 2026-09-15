# Notes

Background that does not fit the [schema](schema.md) or the README.
Deeper per-block history is documented elsewhere; see the observation `provenance` URLs in [`data/invalid-blocks.jsonl`](../data/invalid-blocks.jsonl).

## Replaying full blocks

A `.bin` can be replayed with `bitcoin-cli submitblock`, but what comes back depends on where the violation is caught.
74638 fails the context-free `CheckBlock` checks before anything is stored, so every replay returns `bad-txns-vout-toolarge` on any node.
Its header alone is valid, though: `submitheader` accepts it (noted in <https://github.com/bitcoin-data/stale-blocks/pull/65>), since every header-level check passes and the violation lives entirely in the body.
The transaction-ordering blocks, 474294 and the 2023 sigops blocks fail in `ConnectBlock`, which a node only runs when it is about to extend its active chain with the block.
Replayed today they sit on a branch with less work than the tip, so a node seeing them fresh stores them after the context-free checks and never connects them: `submitblock` returns `inconclusive` and the block becomes a `valid-headers` chain tip.
A node that already stores them returns `duplicate`, and only a node that attempted the connect at their original tip and marked them failed returns `duplicate-invalid` (the result reported in <https://github.com/bitcoin-data/stale-blocks/pull/11>).

## Incident notes

### 507514, 509557, 515319 and 534339 - AntPool parent-transaction reuse (2018)

Each block includes non-coinbase transactions already confirmed in the canonical parent named by its header.
Those transactions' inputs were spent by the parent, so reusing them fails `ConnectBlock`'s input lookup with `bad-txns-inputs-missingorspent`.
The dataset calls this `already_confirmed_in_parent`, separately from forward spends and missing unconfirmed parent transactions.

| Height | Date (UTC) | Transactions reused from parent | Non-coinbase transactions in candidate |
| --- | --- | ---: | ---: |
| [507514](../blocks/507514-000000000000000000571c2b98a090c15774cb7400bd4a50b160d488d14055d0.bin) | 2018-02-04 | 338 | 737 |
| [509557](../blocks/509557-00000000000000000027894f0969c2f79a6cdb1231750e048effbd17c88da431.bin) | 2018-02-17 | 1253 | 2322 |
| [515319](../blocks/515319-00000000000000000014c1ee89b61a84e3e30dd9b2c78c9916d323a2775bc613.bin) | 2018-03-27 | 79 | 79 |
| [534339](../blocks/534339-0000000000000000001a04286794b25ff10dfdb1bb601b17280dfc1ef933a0ba.bin) | 2018-07-30 | 218 | 218 |

Block 515319 copies all 79 of its parent's non-coinbase transactions in the same order.
Block 534339 contains a subset of its parent's transactions, in a different order; the other two mix parent transactions with transactions absent from that parent.
All four coinbases contain `Mined by AntPool` tags.
The pattern suggests a mining template whose previous-block hash was refreshed while some or all of its transaction list was retained.
That is an inference from the bodies, not a recovered record of the pool's template construction.
It is related to the 584802 AntPool incident, where the transaction list was dropped while fees remained in the coinbase; these blocks retain transactions from the parent instead.

The pinned chainquery archive contains the hashes in [orphans_chainquery.com.json](https://github.com/NStifter/mergedmonitor/blob/54344d4e355f73eb94bef8d391e8fb6e4a9323a6/fork-analysis/chainquery.com/orphans_chainquery.com.json) and labels their tips `invalid` in [tips.json](https://github.com/NStifter/mergedmonitor/blob/54344d4e355f73eb94bef8d391e8fb6e4a9323a6/fork-analysis/chainquery.com/tips.json).
Only the former is recorded as a scrape observation; the tip labels are background, not proof.
The bodies were imported into stale-blocks in [abee96d](https://github.com/bitcoin-data/stale-blocks/commit/abee96d) without a recorded acquisition path, so they do not establish another observation or direct P2P reception.

CI checks a named intersecting txid in each body against an ordered parent txid list authenticated by the parent's header merkle root, with a canonical-height lookup binding the parent to height minus one.
All four pass python-bitcoinlib 0.12.2's context-free `CheckBlock` checks and the separate witness-commitment check.
Those checks do not replay historical `ConnectBlock`.
A separate replay did: on 17 September 2026 a Bitcoin Core v31.1.0 node, rewound to each block's parent with `invalidateblock`, returned `bad-txns-inputs-missingorspent` from `submitblock` for all four, naming the first reused transaction in each case.
On the known mainnet chain after BIP34 activation, Core skips BIP30 checks below height 1983702; see the [BIP30 guard in validation.cpp](https://github.com/bitcoin/bitcoin/blob/bf8402c8803f085a50df96cb7956033cd252e9ab/src/validation.cpp#L2412).
The named failure here is the reuse of spent inputs, not `bad-txns-BIP30`.

The 2026-09-15 sweep of stale-blocks at [be1e859](https://github.com/bitcoin-data/stale-blocks/commit/be1e8597615c3372aab9ca437a9cd554822b6870) examined all 1086 bodies.
Of the 1073 bodies extending canonical parents, only these four had a non-coinbase intersection; 1069 had none.
Thirteen bodies extended noncanonical parents and were outside the rule's scope.
There were no parse or retrieval failures.
A negative intersection does not establish that a block satisfies every consensus rule.

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
