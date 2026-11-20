"""SQLite: alert de-duplication + rolling price history + Keepa product cache."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                asin           TEXT PRIMARY KEY,
                last_price     REAL,
                last_discount  INTEGER,
                last_alert_ts  INTEGER,
                last_alert_day TEXT
            );
            CREATE TABLE IF NOT EXISTS price_history (
                asin  TEXT,
                ts    INTEGER,
                price REAL
            );
            CREATE INDEX IF NOT EXISTS idx_hist_asin_ts ON price_history(asin, ts);
            CREATE TABLE IF NOT EXISTS product_cache (
                asin        TEXT PRIMARY KEY,
                fetched_at  INTEGER,
                product_json TEXT
            );
            """
        )
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(alerts)").fetchall()}
        if "last_alert_day" not in cols:
            self.conn.execute("ALTER TABLE alerts ADD COLUMN last_alert_day TEXT DEFAULT ''")
        self.conn.commit()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def should_alert(
        self,
        asin: str,
        price: float,
        *,
        cooldown_hours: int = 24,
        no_repeat_same_day: bool = True,
        allow_if_cheaper: bool = True,
    ) -> bool:
        row = self.conn.execute(
            "SELECT last_price, last_alert_ts, last_alert_day FROM alerts WHERE asin = ?",
            (asin,),
        ).fetchone()
        if row is None:
            return True

        now = int(time.time())
        if no_repeat_same_day and (row["last_alert_day"] or "") == self._today():
            if allow_if_cheaper and price < (row["last_price"] or 1e12):
                return True
            return False

        if allow_if_cheaper and price < (row["last_price"] or 1e12):
            return True
        if now - (row["last_alert_ts"] or 0) >= cooldown_hours * 3600:
            return True
        return False

    def record_alert(self, asin: str, price: float, discount: int) -> None:
        self.conn.execute(
            """
            INSERT INTO alerts (asin, last_price, last_discount, last_alert_ts, last_alert_day)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(asin) DO UPDATE SET
                last_price = excluded.last_price,
                last_discount = excluded.last_discount,
                last_alert_ts = excluded.last_alert_ts,
                last_alert_day = excluded.last_alert_day
            """,
            (asin, price, discount, int(time.time()), self._today()),
        )
        self.conn.commit()

    def add_price(self, asin: str, price: float) -> None:
        self.conn.execute(
            "INSERT INTO price_history (asin, ts, price) VALUES (?, ?, ?)",
            (asin, int(time.time()), price),
        )
        self.conn.commit()

    def prune_history(self, days: int = 45) -> None:
        cutoff = int(time.time()) - days * 86400
        self.conn.execute("DELETE FROM price_history WHERE ts < ?", (cutoff,))
        self.conn.commit()

    # --- product cache ------------------------------------------------
    def get_cached_product(self, asin: str, max_age_days: int = 7) -> dict | None:
        row = self.conn.execute(
            "SELECT fetched_at, product_json FROM product_cache WHERE asin = ?",
            (asin,),
        ).fetchone()
        if row is None:
            return None
        if int(time.time()) - int(row["fetched_at"] or 0) > max_age_days * 86400:
            return None
        try:
            data = json.loads(row["product_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def set_cached_product(self, asin: str, product: dict) -> None:
        try:
            payload = json.dumps(product, separators=(",", ":"))
        except (TypeError, ValueError):
            return
        self.conn.execute(
            """
            INSERT INTO product_cache (asin, fetched_at, product_json)
            VALUES (?, ?, ?)
            ON CONFLICT(asin) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                product_json = excluded.product_json
            """,
            (asin, int(time.time()), payload),
        )
        self.conn.commit()

    def prune_product_cache(self, days: int = 14) -> None:
        cutoff = int(time.time()) - days * 86400
        self.conn.execute("DELETE FROM product_cache WHERE fetched_at < ?", (cutoff,))
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
