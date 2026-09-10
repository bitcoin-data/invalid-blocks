"""Block parsing, commitments and sigop counting without mining or API access."""

import unittest

from bitcoin.core import CBlock, COutPoint, CTransaction, CTxIn, CTxOut, CTxWitness, CTxInWitness
from bitcoin.core.script import CScript, CScriptWitness, OP_TRUE

from block_evidence import (
    MAX_MONEY, establishes_rule, read_block, sha256d, sigop_count, witness_sigops,
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
