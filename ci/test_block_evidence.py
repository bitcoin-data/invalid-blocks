"""Block parsing, commitments and sigop counting without mining or API access."""

import json
import unittest

from bitcoin.core import CBlock, CBlockHeader, COutPoint, CTransaction, CTxIn, CTxOut, CTxWitness, CTxInWitness, b2lx
from bitcoin.core.script import CScript, CScriptWitness, OP_TRUE

from block_evidence import (
    MAX_MONEY, coinbase_amounts, confirmed_at_or_after, establishes_rule, omitted_prevouts, p2sh_spend_fails, read_block,
    read_proof, read_transaction, reuses_parent_transaction, sha256d, sigop_count, witness_sigops,
)

# The 123-byte spend included by 89 blocks in April to July 2012, and the transaction that funded it.
P2SH_SPEND = bytes.fromhex(
    "01000000019dc23528f5a5f376da3f3f4efd45be8c5b551abdb8093940e0b313de459a53b00100000026255121029c7187ecea7f09146820075c3a8d"
    "e5d33ffbc293b63228ea1667c8d3796aff3f51aeffffffff0130570500000000001976a9147288ca9e213c54cbb2094f00bcf33bfbce691dbb88ac00000000")
P2SH_FUNDING = bytes.fromhex(
    "0100000001f6ea284ec7521f8a7d094a6cf4e6873098b90f90725ffd372b343189d7a4089c000000006c4930460221009d1055704950ab3b695c7215"
    "0169e5a41ccd7757b15185dc298e85112ca7fea1022100e979474bae5d7cb44e5149af8b146eab1cb237646094c99eae07579981b2eeae0121025801"
    "704c59321b645109931691c996a0ae797cf155a5ece34bdc065e6b5437a1ffffffff02fc0a0300000000001976a9145a3acbc7bbcc97c5ff16f5909c"
    "9d7d3fadb293a888ac801a06000000000017a914e8c300c87986efa84c37c0519929019ef86eb5b48700000000")


def transaction(prev_hash=bytes(32), vout=0xffffffff, amount=1, witness=False):
    """Serialize a one-input, one-output transaction and its stripped form."""
    tx = CTransaction(
        [CTxIn(COutPoint(prev_hash, vout), CScript(b"\x01\x01"))],
        [CTxOut(amount, CScript([OP_TRUE]))],
        witness=CTxWitness([CTxInWitness(CScriptWitness([b"B"]))]) if witness else CTxWitness(),
    )
    return tx.serialize(), tx.serialize({"include_witness": False})


def block(*transactions):
    """Build a merkle-bound test block; PoW is tested separately in the dataset."""
    root = CBlock.build_merkle_tree_from_txids([sha256d(stripped) for _, stripped in transactions])[-1]
    # Keep raw transaction bytes so malformed encodings can be supplied by tests.
    return bytes(36) + root + bytes(12) + bytes([len(transactions)]) + b"".join(wire for wire, _ in transactions)


def coinbase_branch(txids):
    """Sibling hashes from the coinbase up to the merkle root, in RPC/display order, from the library's full tree."""
    tree = CBlock.build_merkle_tree_from_txids(txids)
    branch, offset, width = [], 0, len(txids)
    while width > 1:
        branch.append(b2lx(tree[offset + 1]))
        offset, width = offset + width, (width + 1) >> 1
    return branch


class BlockEvidenceChecks(unittest.TestCase):
    def test_overflow_boundary(self):
        """Only outputs above MAX_MONEY establish the output-too-large failure."""
        for amount, expected in ((-1, False), (MAX_MONEY, False), (MAX_MONEY + 1, True)):
            with self.subTest(amount=amount):
                parsed = read_block(block(transaction(amount=amount)))
                self.assertEqual(establishes_rule(parsed, "bad-txns-vout-toolarge"), expected)

    def test_forward_spend_requires_later_transaction_and_existing_output(self):
        """Ordering evidence must identify an existing output created later in the block."""
        coinbase = transaction()
        producer = transaction(prev_hash=b"\x11" * 32, vout=0)
        consumer = transaction(prev_hash=sha256d(producer[1]), vout=0)
        rule = "bad-txns-inputs-missingorspent"
        self.assertTrue(establishes_rule(read_block(block(coinbase, consumer, producer)), rule))
        self.assertFalse(establishes_rule(read_block(block(coinbase, producer, consumer)), rule))
        bad_index = transaction(prev_hash=sha256d(producer[1]), vout=1)
        self.assertFalse(establishes_rule(read_block(block(coinbase, bad_index, producer)), rule))
        self.assertFalse(establishes_rule(read_block(block(coinbase, producer)), rule))

    def test_missing_parent_boundaries(self):
        """Only outside-block spends are omitted prevouts; the parent must be confirmed at this height or later elsewhere."""
        coinbase = transaction()
        parent = transaction(prev_hash=b"\x11" * 32, vout=0)
        consumer = transaction(prev_hash=sha256d(parent[1]), vout=0)
        self.assertEqual(omitted_prevouts(read_block(block(coinbase, consumer)).vtx), [(sha256d(parent[1]), 0)])
        self.assertEqual(omitted_prevouts(read_block(block(coinbase, parent, consumer)).vtx), [(b"\x11" * 32, 0)])
        candidate = "ab" * 32
        for confirmation, expected in (((99, "cd" * 32), False), ((100, "cd" * 32), True),
                                       ((101, "cd" * 32), True), ((100, candidate), False)):
            with self.subTest(confirmation=confirmation):
                self.assertEqual(confirmed_at_or_after(confirmation, 100, candidate), expected)

    def test_invalid_block_serialization(self):
        """Reject malformed bodies, transaction commitments and witness encodings."""
        tx = transaction()
        data = block(tx)
        witness = transaction(witness=True)
        cases = {
            "header only": data[:80],
            "truncated transaction": data[:-1],
            "trailing bytes": data + b"\x00",
            "noncanonical count": data[:80] + b"\xfd\x01\x00" + data[81:],
            "empty block": bytes(80) + b"\x00",
            "merkle mismatch": data[:36] + bytes(32) + data[68:],
            "duplicate txids": block(tx, tx),
            "missing coinbase": block(transaction(prev_hash=b"\x11" * 32, vout=0)),
            "unknown witness flag": block((witness[0][:5] + b"\x02" + witness[0][6:], witness[1])),
            "empty witness stacks": block((witness[0][:-7] + b"\x00" + witness[0][-4:], witness[1])),
        }
        for case, wire in cases.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                read_block(wire)

    def test_p2sh_spend_passes_legacy_and_fails_p2sh(self):
        """The 2012 spend fails only once the redeem script runs; a spend that fails regardless is not evidence."""
        spend, funding = read_transaction(P2SH_SPEND), read_transaction(P2SH_FUNDING)
        self.assertEqual(spend.vin[0].prevout.hash, funding.GetTxid())
        self.assertTrue(p2sh_spend_fails(spend, 0, funding))
        wrong = CTransaction([CTxIn(spend.vin[0].prevout, CScript([b"\x51"]))], spend.vout)
        with self.subTest(case="fails without P2SH"), self.assertRaisesRegex(ValueError, "even without P2SH"):
            p2sh_spend_fails(wrong, 0, funding)

    def test_parent_transaction_reuse_excludes_coinbases_and_absent_transactions(self):
        """The named witness must be a non-coinbase transaction present in both blocks."""
        coinbase = transaction()
        reused = transaction(prev_hash=b"\x11" * 32, vout=0)
        candidate = read_block(block(coinbase, reused))
        txid = b2lx(candidate.vtx[1].GetTxid())
        coinbase_id = b2lx(candidate.vtx[0].GetTxid())
        cases = (
            ("intersection", candidate, ["ab" * 32, txid], txid, True),
            ("disjoint", candidate, ["ab" * 32, "cd" * 32], txid, False),
            ("body coinbase", candidate, ["ab" * 32, coinbase_id], coinbase_id, False),
            ("parent coinbase", candidate, [txid, "ab" * 32], txid, False),
            ("absent from body", candidate, ["ab" * 32, "cd" * 32], "cd" * 32, False),
            ("coinbase only", read_block(block(coinbase)), ["ab" * 32, txid], txid, False),
        )
        for name, body, parent, witness, expected in cases:
            with self.subTest(case=name):
                self.assertEqual(reuses_parent_transaction(body, parent, witness), expected)

    def test_coinbase_branch_proof_places_only_a_coinbase_at_index_zero(self):
        """A branch of siblings from the coinbase reproduces the root; a wrong sibling or a non-coinbase transaction does not."""
        coinbase, spend, other = transaction(), transaction(prev_hash=b"\x11" * 32, vout=0), transaction(prev_hash=b"\x22" * 32, vout=0)
        body = block(coinbase, spend, other)
        header = CBlockHeader.deserialize(body[:80])
        branch = coinbase_branch([sha256d(stripped) for _, stripped in (coinbase, spend, other)])
        proof = {"transaction": coinbase[0].hex(), "merkle_branch": branch}
        self.assertTrue(read_proof(json.dumps(proof).encode(), header).is_coinbase())
        with self.subTest(case="single-transaction block"):
            single = CBlockHeader.deserialize(block(coinbase)[:80])
            self.assertTrue(read_proof(json.dumps(dict(proof, merkle_branch=[])).encode(), single).is_coinbase())
        cases = (
            ("wrong sibling", dict(proof, merkle_branch=branch[::-1]), "does not reproduce"),
            ("spend with a branch", dict(proof, transaction=spend[0].hex()), "requires a coinbase"),
        )
        for case, content, error in cases:
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, error):
                read_proof(json.dumps(content).encode(), header)


class CoinbaseAmountChecks(unittest.TestCase):
    def test_subsidy_boundaries(self):
        """Require a strict overpayment, including halving and zero-subsidy boundaries."""
        for height, subsidy in ((209999, 5_000_000_000), (210000, 2_500_000_000),
                                (584802, 1_250_000_000), (64 * 210000, 0)):
            for excess in (0, 1):
                with self.subTest(height=height, excess=excess):
                    body = read_block(block(transaction(amount=subsidy + excess)))
                    self.assertEqual(coinbase_amounts(body, height, {}),
                                     {"coinbase": subsidy + excess, "subsidy": subsidy,
                                      "fees": 0, "excess": excess})

    def test_authenticated_fees_and_in_block_spends(self):
        """Account for external and earlier in-block outputs without fetching internal ones."""
        parent = read_transaction(transaction(prev_hash=b"\x11" * 32, vout=0, amount=100)[0])
        first = transaction(prev_hash=parent.GetTxid(), vout=0, amount=90)
        second = transaction(prev_hash=sha256d(first[1]), vout=0, amount=80)
        for excess in (0, 1):
            with self.subTest(excess=excess):
                body = read_block(block(transaction(amount=1_250_000_020 + excess), first, second))
                amounts = coinbase_amounts(body, 584802, {parent.GetTxid(): parent})
                self.assertEqual(amounts["fees"], 20)
                self.assertEqual(amounts["excess"], excess)

    def test_unusable_fee_evidence_raises(self):
        """Missing, repeated or negative-fee inputs cannot prove overpayment."""
        parent = read_transaction(transaction(prev_hash=b"\x11" * 32, vout=0, amount=100)[0])
        first = transaction(prev_hash=parent.GetTxid(), vout=0, amount=90)
        second = transaction(prev_hash=sha256d(first[1]), vout=0, amount=80)
        previous = {parent.GetTxid(): parent}
        cb = transaction(amount=1_250_000_021)
        cases = {
            "missing": (block(cb, first), {}, "missing previous output"),
            "missing vout": (block(cb, transaction(prev_hash=parent.GetTxid(), vout=1)), previous, "missing previous output"),
            "double spend": (block(cb, first, transaction(prev_hash=parent.GetTxid(), vout=0, amount=80)), previous, "repeated input"),
            "negative fee": (block(cb, transaction(prev_hash=parent.GetTxid(), vout=0, amount=101)), previous, "negative fee"),
            "negative output": (block(transaction(amount=-1)), {}, "money range"),
        }
        for case, (raw, prevouts, error) in cases.items():
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, error):
                coinbase_amounts(read_block(raw), 584802, prevouts)


class SigopChecks(unittest.TestCase):
    def test_legacy_multisig_accurate_count_and_pushed_bytes(self):
        """Check multisig counting modes, ignore pushed opcodes and stop at malformed pushes."""
        self.assertEqual(sigop_count(bytes.fromhex("52aeac")), 21)
        self.assertEqual(sigop_count(bytes.fromhex("52aeac"), accurate=True), 3)
        self.assertEqual(sigop_count(bytes.fromhex("0050ae"), accurate=True), 20)
        self.assertEqual(sigop_count(bytes.fromhex("02acaeac")), 1)
        self.assertEqual(sigop_count(bytes.fromhex("ac4c02ac")), 1)  # malformed push ends counting

    def test_witness_script_accurate_count_and_taproot_exclusion(self):
        """Count P2WSH sigops; empty stacks, Taproot and malformed programs contribute zero."""
        self.assertEqual(witness_sigops(b"\x00\x20" + bytes(32), [bytes.fromhex("53aeac")]), 4)
        self.assertEqual(witness_sigops(b"\x00\x20" + bytes(32), []), 0)
        self.assertEqual(witness_sigops(b"\x51\x20" + bytes(32), [b"\xac"]), 0)
        self.assertEqual(witness_sigops(b"\x00\x14" + bytes(19), []), 0)


if __name__ == "__main__":
    unittest.main()
