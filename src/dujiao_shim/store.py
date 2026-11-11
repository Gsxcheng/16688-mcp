"""SQLite order ledger. Order rows are written BEFORE any money moves so a crash can never double-buy."""
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT UNIQUE NOT NULL,
  downstream_order_no TEXT UNIQUE,
  sku_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  status TEXT NOT NULL,            -- processing | awaiting_manual | needs_review | delivered | canceled | rejected
  supplier TEXT NOT NULL DEFAULT '',
  supplier_ref TEXT NOT NULL DEFAULT '',   -- 16688 trade_no
  amount TEXT NOT NULL DEFAULT '0.00',
  payload TEXT NOT NULL DEFAULT '',        -- delivered card codes / content (sensitive)
  error TEXT NOT NULL DEFAULT '',
  callback_url TEXT NOT NULL DEFAULT '',
  callback_state TEXT NOT NULL DEFAULT 'none',   -- none | pending | done | failed
  callback_attempts INTEGER NOT NULL DEFAULT 0,
  next_callback_at REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  delivered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
"""

# 'rejected' = refused before any money moved; Dujiao may retry the same downstream_order_no.
OPEN_STATES = ("processing", "awaiting_manual", "needs_review")


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _one(self, sql: str, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def _all(self, sql: str, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _run(self, sql: str, args=()):
        with self._lock:
            return self._db.execute(sql, args)

    def create(self, sku_id: int, product_id: int, quantity: int, downstream_order_no: str | None,
               callback_url: str) -> sqlite3.Row:
        now = time.time()
        cur = self._run(
            "INSERT INTO orders(order_no, downstream_order_no, sku_id, product_id, quantity, status,"
            " callback_url, created_at, updated_at) VALUES('', ?, ?, ?, ?, 'processing', ?, ?, ?)",
            (downstream_order_no or None, sku_id, product_id, quantity, callback_url, now, now))
        oid = cur.lastrowid
        self._run("UPDATE orders SET order_no=? WHERE id=?", (f"SH{int(now)}{oid:05d}", oid))
        return self.get(oid)

    def get(self, order_id: int):
        return self._one("SELECT * FROM orders WHERE id=?", (order_id,))

    def get_by_no(self, order_no: str):
        return self._one("SELECT * FROM orders WHERE order_no=?", (order_no,))

    def get_by_downstream(self, downstream_order_no: str):
        return self._one("SELECT * FROM orders WHERE downstream_order_no=?", (downstream_order_no,))

    def update(self, order_id: int, **fields) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._run(f"UPDATE orders SET {cols} WHERE id=?", (*fields.values(), order_id))

    def reopen(self, order_id: int, callback_url: str) -> None:
        self.update(order_id, status="processing", error="", supplier="", supplier_ref="", callback_url=callback_url)

    def finish(self, order_id: int, status: str, payload: str = "", error: str = "") -> None:
        """Move to a terminal state (delivered / canceled) and queue the Dujiao callback."""
        extra = {"delivered_at": time.time()} if status == "delivered" else {}
        self.update(order_id, status=status, payload=payload, error=error,
                    callback_state="pending", callback_attempts=0, next_callback_at=0, **extra)

    def due_callbacks(self, now: float):
        return self._all("SELECT * FROM orders WHERE callback_state='pending' AND next_callback_at<=?", (now,))

    def polling_orders(self):
        return self._all("SELECT * FROM orders WHERE status='processing' AND supplier='16688' AND supplier_ref!=''")

    def interrupted_purchases(self):
        return self._all("SELECT * FROM orders WHERE status='processing' AND supplier='16688' AND supplier_ref=''")

    def pending_manual(self):
        return self._all("SELECT * FROM orders WHERE status IN ('awaiting_manual','needs_review') ORDER BY id")

    def orders_today(self, since: float) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM orders WHERE created_at>=? AND supplier='16688'", (since,))
        return row["n"]
