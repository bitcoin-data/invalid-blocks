# Notes

Background that does not fit the [schema](schema.md) or the README.
Deeper per-block history is documented elsewhere; see the observation `provenance` URLs in [`data/invalid-blocks.jsonl`](../data/invalid-blocks.jsonl).

## Replaying full blocks

A `.bin` can be replayed with `bitcoin-cli submitblock`, but what comes back depends on where the violation is caught.
74638 fails the context-free `CheckBlock` checks before anything is stored, so every replay returns `bad-txns-vout-toolarge` on any node.
Its header alone is valid, though: `submitheader` accepts it (noted in <https://github.com/bitcoin-data/stale-blocks/pull/65>), since every header-level check passes and the violation lives entirely in the body.
The transaction-ordering and 2023 sigops blocks fail in `ConnectBlock`, which never runs for a deep side-chain block: a node seeing them fresh returns `inconclusive` and stores the block as a `valid-headers` chain tip, a node that already stores them returns `duplicate`, and only a node that attempted the connect at their original tip and marked them failed returns `duplicate-invalid` (the result reported in <https://github.com/bitcoin-data/stale-blocks/pull/11>).

## Incident notes

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
