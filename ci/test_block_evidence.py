"""Block parsing, commitments and sigop counting without mining or API access."""

import unittest

from bitcoin.core import CBlock, COutPoint, CTransaction, CTxIn, CTxOut, CTxWitness, CTxInWitness, b2lx
from bitcoin.core.script import CScript, CScriptWitness, OP_TRUE

from block_evidence import (
    MAX_MONEY, coinbase_amounts, confirmed_at_or_after, establishes_rule, omitted_prevouts, read_block, read_transaction,
    reuses_parent_transaction, sha256d, sigop_count, witness_sigops,
)


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
