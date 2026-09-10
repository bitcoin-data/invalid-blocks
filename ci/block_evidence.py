"""Read committed transactions for narrow, offline evidence checks.

This is not a consensus validator: no script execution, UTXO lookup or
historical chain reconstruction takes place here.
Transaction IDs and the merkle root bind the checked non-witness data to the
Bitcoin header. Parsing consumes the entire file so truncation cannot satisfy
a rule's requirement for a complete block body.

Sigop counting mirrors Core's CScript::GetSigOpCount, GetTransactionSigOpCost
and CountWitnessSigOps for mainnet after SegWit activation. It counts operations,
not signature executions: branches are counted even if they would not execute.
Taproot does not contribute to the legacy block sigop cost (it has a separate
per-input validation budget).
"""

from collections.abc import Mapping, Sequence
from typing import TypeVar

from bitcoin.core import CBlock, CoreMainParams, CTransaction, Hash as sha256d, b2lx
from bitcoin.core.script import (
    CScript, CScriptInvalidError, CScriptOp, OP_1, OP_16,
    OP_CHECKSIG, OP_CHECKSIGVERIFY, OP_CHECKMULTISIG, OP_CHECKMULTISIGVERIFY,
)
from bitcoin.core.serialize import SerializationError

T = TypeVar("T", CBlock, CTransaction)

MAX_MONEY = CoreMainParams.MAX_MONEY
MAX_BLOCK_SIGOPS_COST = 80_000


def deserialize(data: bytes, cls: type[T]) -> T:
    """Parse wire bytes without consensus validation, rejecting normalized encodings."""
    try:
        value = cls.deserialize(data)
        if value.serialize() != data:
            raise ValueError("non-canonical or superfluous wire encoding")
    except SerializationError as exc:
        raise ValueError(f"invalid or truncated wire data: {exc}") from exc
    transactions = value.vtx if isinstance(value, CBlock) else (value,)
    if any(not tx.vin or not tx.vout for tx in transactions):
        raise ValueError("transaction has no inputs or outputs")
    return value


def read_transaction(data: bytes) -> CTransaction:
    """Read a complete transaction, retaining its original txid and witness bytes."""
    return deserialize(data, CTransaction)


def verify_witness_commitment(block: CBlock) -> None:
    """Bind witness scripts used in sigop counting to the coinbase commitment.

    The highest matching output wins (BIP141). The coinbase wtxid is zero;
    its witness must supply exactly one 32-byte reserved value. The caller
    must already have verified the transaction merkle root against the header.
    """
    try:
        output = block.vtx[0].vout[block.get_witness_commitment_index()]
    except ValueError:
        if any(tx.has_witness() for tx in block.vtx):
            raise ValueError("witness data without a coinbase commitment")
        return
    coinbase = block.vtx[0]
    reserved = coinbase.wit.vtxinwit[0].scriptWitness.stack if coinbase.has_witness() else ()
    if len(reserved) != 1 or len(reserved[0]) != 32:
        raise ValueError("coinbase witness must contain one 32-byte reserved value")
    if sha256d(block.calc_witness_merkle_root() + reserved[0]) != output.scriptPubKey[6:38]:
        raise ValueError("witness merkle commitment mismatch")


def read_block(data: bytes) -> CBlock:
    """Parse a complete body and verify its transaction merkle commitment.

    Duplicate txids are excluded from this evidence path: they make an
    in-block outpoint lookup ambiguous and can produce a mutated merkle tree.
    Such incidents need a separate rule and evidence checker if added later.
    """
    block = deserialize(data, CBlock)
    transactions = block.vtx
    if not transactions or not transactions[0].is_coinbase():
        raise ValueError("first transaction is not a coinbase")
    hashes = [tx.GetTxid() for tx in transactions]
    if len(set(hashes)) != len(hashes):
        raise ValueError("duplicate transaction IDs in block evidence")
    if block.calc_merkle_root() != block.hashMerkleRoot:
        raise ValueError("transaction merkle root does not match header")
    return block


def omitted_prevouts(transactions: Sequence[CTransaction]) -> list[tuple[bytes, int]]:
    """Return external prevouts spent by non-coinbase inputs, as (txid, vout)."""
    own = {tx.GetTxid() for tx in transactions}
    return [(txin.prevout.hash, txin.prevout.n)
            for tx in transactions[1:] for txin in tx.vin if txin.prevout.hash not in own]


def establishes_rule(block: CBlock, rule: str) -> bool:
    """Recognize only failures provable from these committed transactions.

    A forward spend must name an existing output of a later transaction.
    Missing external inputs and sigop costs need historical prevout data and
    deliberately do not count as proofs here.
    """
    transactions = block.vtx
    if rule == "bad-txns-vout-toolarge":
        return any(output.nValue > MAX_MONEY for tx in transactions for output in tx.vout)
    if rule == "bad-txns-inputs-missingorspent":
        positions = {tx.GetTxid(): i for i, tx in enumerate(transactions)}
        for index, tx in enumerate(transactions):
            for txin in tx.vin:
                later = positions.get(txin.prevout.hash)
                if later is not None and later > index and txin.prevout.n < len(transactions[later].vout):
                    return True
    return False


def establishes_missing_unconfirmed_parent(
        block: CBlock, height: int, block_hash: str,
        previous_transactions: Mapping[bytes, CTransaction],
        confirmations: Mapping[bytes, tuple[int, str]]) -> bool:
    """True when a spend names an output currently confirmed at or after this height elsewhere.

    confirmations maps raw txid to (block_height, display-order block hash)
    from a provider's current canonical status. Unconfirmed prevouts and
    parents confirmed below the candidate height are not evidence. Duplicate
    txids and later re-confirmation can make that height ambiguous; this
    check does not reconstruct a UTXO set.
    """
    for txid, vout in omitted_prevouts(block.vtx):
        confirmation = confirmations.get(txid)
        previous = previous_transactions.get(txid)
        if confirmation is None or previous is None or vout >= len(previous.vout):
            continue
        confirmed_height, confirmed_hash = confirmation
        if confirmed_height >= height and confirmed_hash != block_hash:
            return True
    return False


def sigop_count(script: bytes, accurate: bool = False) -> int:
    """Count CHECKSIG as one and CHECKMULTISIG as 20 or preceding OP_N.

    As in Core, a malformed push ends counting at that point. Bytes inside
    pushed data are not opcodes. Accurate counting is for redeem/witness
    scripts; the legacy input/output count always uses 20 for multisig.
    """
    # python-bitcoinlib 0.12.2's GetSigOpCount(True) raises on OP_N MULTISIG;
    # its iterator also raises on malformed pushes instead of keeping the count.
    count = 0
    previous = None
    try:
        for opcode, _, _ in CScript(script).raw_iter():
            if opcode in (OP_CHECKSIG, OP_CHECKSIGVERIFY):
                count += 1
            elif opcode in (OP_CHECKMULTISIG, OP_CHECKMULTISIGVERIFY):
                count += CScriptOp(previous).decode_op_n() if accurate and previous is not None and OP_1 <= previous <= OP_16 else 20
            previous = opcode
    except CScriptInvalidError:
        pass
    return count


def last_push(script: bytes) -> bytes | None:
    """Return Core's last pushed byte vector, or None for non-push-only input."""
    script = CScript(script)
    if not script.is_push_only():
        return None
    data = None
    for _, data, _ in script.raw_iter():
        pass
    return data or b""


def witness_sigops(program_script: bytes, witness: Sequence[bytes]) -> int:
    """Count version-0 witness programs; other versions add no block cost."""
    script = CScript(program_script)
    if script.is_witness_v0_keyhash():
        return 1
    if script.is_witness_v0_scripthash() and witness:
        return sigop_count(witness[-1], accurate=True)
    return 0


def sigop_cost(block: CBlock, previous_transactions: Mapping[bytes, CTransaction]) -> dict[str, int]:
    """Calculate legacy, P2SH and witness costs, requiring every prevout.

    previous_transactions is keyed by raw txid, verified when loading its
    stripped bytes. Only earlier in-block outputs are made available while
    walking the block. The witness commitment is checked before using witness
    scripts, which are not covered by the transaction IDs alone.
    This guard also protects callers outside the dataset validation pipeline.
    """
    verify_witness_commitment(block)
    available = dict(previous_transactions)
    totals = {"legacy": 0, "p2sh": 0, "witness": 0}
    for index, tx in enumerate(block.vtx):
        totals["legacy"] += 4 * sum(sigop_count(txin.scriptSig) for txin in tx.vin)
        totals["legacy"] += 4 * sum(sigop_count(output.scriptPubKey) for output in tx.vout)
        if index:
            for input_index, txin in enumerate(tx.vin):
                txid, vout = txin.prevout.hash, txin.prevout.n
                witness = tx.wit.vtxinwit[input_index].scriptWitness.stack if tx.has_witness() else ()
                previous = available.get(txid)
                if previous is None or vout >= len(previous.vout):
                    raise ValueError(f"missing previous output {b2lx(txid)}:{vout}")
                script_pubkey = previous.vout[vout].scriptPubKey
                redeem = last_push(txin.scriptSig) if script_pubkey.is_p2sh() else None
                if redeem is not None:
                    totals["p2sh"] += 4 * sigop_count(redeem, accurate=True)
                program = redeem if redeem is not None else script_pubkey
                totals["witness"] += witness_sigops(program, witness)
        available[tx.GetTxid()] = tx
    totals["total"] = sum(totals.values())
    return totals
