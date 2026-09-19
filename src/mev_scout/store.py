"""SQLite store for liquidations, fetched ranges, and RPC call caching."""

from pathlib import Path
import sqlite3
from typing import Any

from mev_scout.decode import Liquidation
from mev_scout.dex import Pool
from mev_scout.swaps import DecodedSwap


class Store:
    def __init__(self, db_path: str | Path = ":memory:"):
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(db_path)
        elif db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS liquidations (
                    chain_id INTEGER NOT NULL,
                    block INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    collateral TEXT NOT NULL,
                    debt TEXT NOT NULL,
                    user TEXT NOT NULL,
                    debt_to_cover TEXT NOT NULL,
                    collateral_amount TEXT NOT NULL,
                    liquidator TEXT NOT NULL,
                    receive_atoken INTEGER NOT NULL,
                    gas_used INTEGER NOT NULL,
                    gas_price TEXT NOT NULL,
                    PRIMARY KEY (chain_id, tx_hash, log_index)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fetched_ranges (
                    chain_id INTEGER NOT NULL,
                    from_block INTEGER NOT NULL,
                    to_block INTEGER NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS call_cache (
                    chain_id INTEGER NOT NULL,
                    to_address TEXT NOT NULL,
                    data TEXT NOT NULL,
                    block_number TEXT NOT NULL,
                    result TEXT NOT NULL,
                    PRIMARY KEY (chain_id, to_address, data, block_number)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decimals_cache (
                    chain_id INTEGER NOT NULL,
                    token_address TEXT NOT NULL,
                    decimals INTEGER NOT NULL,
                    PRIMARY KEY (chain_id, token_address)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pools (
                    chain_id INTEGER NOT NULL,
                    dex TEXT NOT NULL,
                    address TEXT NOT NULL,
                    token0 TEXT NOT NULL,
                    token1 TEXT NOT NULL,
                    fee INTEGER NOT NULL,
                    PRIMARY KEY (chain_id, address)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS swaps (
                    chain_id INTEGER NOT NULL,
                    dex TEXT NOT NULL,
                    pool TEXT NOT NULL,
                    block INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    amount0 TEXT NOT NULL,
                    amount1 TEXT NOT NULL,
                    sqrt_price_x96 TEXT NOT NULL,
                    liquidity TEXT NOT NULL,
                    tick INTEGER NOT NULL,
                    protocol_fees_token0 TEXT NOT NULL DEFAULT '0',
                    protocol_fees_token1 TEXT NOT NULL DEFAULT '0',
                    PRIMARY KEY (chain_id, tx_hash, log_index)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pool_fetched_ranges (
                    chain_id INTEGER NOT NULL,
                    pool TEXT NOT NULL,
                    from_block INTEGER NOT NULL,
                    to_block INTEGER NOT NULL
                )
                """
            )

    def insert_liquidations(self, items: list[Liquidation]) -> None:
        if not items:
            return
        rows = [
            (
                item.chain_id,
                item.block,
                item.timestamp,
                item.tx_hash.lower(),
                item.log_index,
                item.collateral.lower(),
                item.debt.lower(),
                item.user.lower(),
                str(item.debt_to_cover),
                str(item.collateral_amount),
                item.liquidator.lower(),
                1 if item.receive_atoken else 0,
                item.gas_used,
                str(item.gas_price),
            )
            for item in items
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO liquidations (
                    chain_id, block, timestamp, tx_hash, log_index,
                    collateral, debt, user, debt_to_cover, collateral_amount,
                    liquidator, receive_atoken, gas_used, gas_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_liquidations(
        self,
        chain_id: int,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> list[Liquidation]:
        query = (
            "SELECT chain_id, block, timestamp, tx_hash, log_index, "
            "collateral, debt, user, debt_to_cover, collateral_amount, "
            "liquidator, receive_atoken, gas_used, gas_price "
            "FROM liquidations WHERE chain_id = ?"
        )
        params: list[Any] = [chain_id]
        if from_block is not None:
            query += " AND block >= ?"
            params.append(from_block)
        if to_block is not None:
            query += " AND block <= ?"
            params.append(to_block)
        query += " ORDER BY block ASC, log_index ASC"

        cur = self.conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        return [
            Liquidation(
                chain_id=r[0],
                block=r[1],
                timestamp=r[2],
                tx_hash=r[3],
                log_index=r[4],
                collateral=r[5],
                debt=r[6],
                user=r[7],
                debt_to_cover=int(r[8]),
                collateral_amount=int(r[9]),
                liquidator=r[10],
                receive_atoken=bool(r[11]),
                gas_used=r[12],
                gas_price=int(r[13]),
            )
            for r in rows
        ]

    def insert_range(self, chain_id: int, from_block: int, to_block: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO fetched_ranges (chain_id, from_block, to_block) VALUES (?, ?, ?)",
                (chain_id, from_block, to_block),
            )

    def last_fetched_block(self, chain_id: int) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(to_block) FROM fetched_ranges WHERE chain_id = ?", (chain_id,)
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def covered(self, chain_id: int, from_block: int, to_block: int) -> list[tuple[int, int]]:
        if from_block > to_block:
            return []

        cur = self.conn.cursor()
        cur.execute(
            "SELECT from_block, to_block FROM fetched_ranges WHERE chain_id = ? ORDER BY from_block ASC",
            (chain_id,),
        )
        raw_ranges = cur.fetchall()
        if not raw_ranges:
            return [(from_block, to_block)]

        # Merge overlapping and adjacent ranges
        merged: list[tuple[int, int]] = []
        for f, t in sorted(raw_ranges, key=lambda r: (r[0], r[1])):
            if not merged:
                merged.append((f, t))
            else:
                prev_f, prev_t = merged[-1]
                if f <= prev_t + 1:
                    merged[-1] = (prev_f, max(prev_t, t))
                else:
                    merged.append((f, t))

        # Find gaps within [from_block, to_block]
        gaps: list[tuple[int, int]] = []
        cursor = from_block

        for start, end in merged:
            if end < cursor:
                continue
            if start > cursor:
                gaps.append((cursor, min(to_block, start - 1)))
                if start > to_block:
                    cursor = to_block + 1
                    break
                cursor = end + 1
            else:
                cursor = max(cursor, end + 1)

            if cursor > to_block:
                break

        if cursor <= to_block:
            gaps.append((cursor, to_block))

        return gaps

    def get_call_cache(
        self,
        chain_id: int,
        to_address: str,
        data: str,
        block: int | str,
    ) -> str | None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT result FROM call_cache WHERE chain_id = ? AND to_address = ? AND data = ? AND block_number = ?",
            (chain_id, to_address.lower(), data.lower(), str(block).lower()),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def set_call_cache(
        self,
        chain_id: int,
        to_address: str,
        data: str,
        block: int | str,
        result: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO call_cache (chain_id, to_address, data, block_number, result)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chain_id, to_address.lower(), data.lower(), str(block).lower(), result),
            )

    def get_decimals(self, chain_id: int, token_address: str) -> int | None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT decimals FROM decimals_cache WHERE chain_id = ? AND token_address = ?",
            (chain_id, token_address.lower()),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def set_decimals(self, chain_id: int, token_address: str, decimals: int) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO decimals_cache (chain_id, token_address, decimals)
                VALUES (?, ?, ?)
                """,
                (chain_id, token_address.lower(), decimals),
            )

    def insert_pools(self, pools: list[Pool]) -> None:
        if not pools:
            return
        rows = [
            (
                p.chain_id,
                p.dex,
                p.address.lower(),
                p.token0.lower(),
                p.token1.lower(),
                p.fee,
            )
            for p in pools
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO pools (
                    chain_id, dex, address, token0, token1, fee
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_pools(self, chain_id: int, dex: str | None = None) -> list[Pool]:
        query = "SELECT chain_id, dex, address, token0, token1, fee FROM pools WHERE chain_id = ?"
        params: list[Any] = [chain_id]
        if dex is not None:
            query += " AND dex = ?"
            params.append(dex)
        query += " ORDER BY dex ASC, address ASC"
        cur = self.conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        return [
            Pool(
                chain_id=r[0],
                dex=r[1],
                address=r[2],
                token0=r[3],
                token1=r[4],
                fee=r[5],
            )
            for r in rows
        ]

    def get_pool(self, chain_id: int, address: str) -> Pool | None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT chain_id, dex, address, token0, token1, fee FROM pools WHERE chain_id = ? AND address = ?",
            (chain_id, address.lower()),
        )
        row = cur.fetchone()
        if not row:
            return None
        return Pool(
            chain_id=row[0],
            dex=row[1],
            address=row[2],
            token0=row[3],
            token1=row[4],
            fee=row[5],
        )

    def insert_swaps(self, items: list[DecodedSwap]) -> None:
        if not items:
            return
        rows = [
            (
                s.chain_id,
                s.dex,
                s.pool.lower(),
                s.block,
                s.timestamp,
                s.tx_hash.lower(),
                s.log_index,
                s.sender.lower(),
                s.recipient.lower(),
                str(s.amount0),
                str(s.amount1),
                str(s.sqrt_price_x96),
                str(s.liquidity),
                s.tick,
                str(s.protocol_fees_token0),
                str(s.protocol_fees_token1),
            )
            for s in items
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO swaps (
                    chain_id, dex, pool, block, timestamp, tx_hash, log_index,
                    sender, recipient, amount0, amount1, sqrt_price_x96,
                    liquidity, tick, protocol_fees_token0, protocol_fees_token1
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_swaps(
        self,
        chain_id: int,
        from_block: int | None = None,
        to_block: int | None = None,
        pool: str | None = None,
    ) -> list[DecodedSwap]:
        query = (
            "SELECT chain_id, dex, pool, block, timestamp, tx_hash, log_index, "
            "sender, recipient, amount0, amount1, sqrt_price_x96, liquidity, "
            "tick, protocol_fees_token0, protocol_fees_token1 "
            "FROM swaps WHERE chain_id = ?"
        )
        params: list[Any] = [chain_id]
        if from_block is not None:
            query += " AND block >= ?"
            params.append(from_block)
        if to_block is not None:
            query += " AND block <= ?"
            params.append(to_block)
        if pool is not None:
            query += " AND pool = ?"
            params.append(pool.lower())
        query += " ORDER BY block ASC, log_index ASC"

        cur = self.conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        return [
            DecodedSwap(
                chain_id=r[0],
                dex=r[1],
                pool=r[2],
                block=r[3],
                timestamp=r[4],
                tx_hash=r[5],
                log_index=r[6],
                sender=r[7],
                recipient=r[8],
                amount0=int(r[9]),
                amount1=int(r[10]),
                sqrt_price_x96=int(r[11]),
                liquidity=int(r[12]),
                tick=r[13],
                protocol_fees_token0=int(r[14]),
                protocol_fees_token1=int(r[15]),
            )
            for r in rows
        ]

    def insert_pool_range(self, chain_id: int, pool: str, from_block: int, to_block: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO pool_fetched_ranges (chain_id, pool, from_block, to_block) VALUES (?, ?, ?, ?)",
                (chain_id, pool.lower(), from_block, to_block),
            )

    def last_fetched_pool_block(self, chain_id: int, pool: str) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(to_block) FROM pool_fetched_ranges WHERE chain_id = ? AND pool = ?",
            (chain_id, pool.lower()),
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def pool_covered(self, chain_id: int, pool: str, from_block: int, to_block: int) -> list[tuple[int, int]]:
        if from_block > to_block:
            return []

        cur = self.conn.cursor()
        cur.execute(
            "SELECT from_block, to_block FROM pool_fetched_ranges WHERE chain_id = ? AND pool = ? ORDER BY from_block ASC",
            (chain_id, pool.lower()),
        )
        raw_ranges = cur.fetchall()
        if not raw_ranges:
            return [(from_block, to_block)]

        merged: list[tuple[int, int]] = []
        for f, t in sorted(raw_ranges, key=lambda r: (r[0], r[1])):
            if not merged:
                merged.append((f, t))
            else:
                prev_f, prev_t = merged[-1]
                if f <= prev_t + 1:
                    merged[-1] = (prev_f, max(prev_t, t))
                else:
                    merged.append((f, t))

        gaps: list[tuple[int, int]] = []
        cursor = from_block

        for start, end in merged:
            if end < cursor:
                continue
            if start > cursor:
                gaps.append((cursor, min(to_block, start - 1)))
                if start > to_block:
                    cursor = to_block + 1
                    break
                cursor = end + 1
            else:
                cursor = max(cursor, end + 1)

            if cursor > to_block:
                break

        if cursor <= to_block:
            gaps.append((cursor, to_block))

        return gaps

    def close(self) -> None:
        self.conn.close()
