"""Penyimpan riwayat pembacaan (SQLite) + statistik ringkas."""

from __future__ import annotations

import csv
import os
import sqlite3
import statistics
import threading
import time

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "history.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    device  TEXT NOT NULL,
    point   TEXT NOT NULL,
    value   REAL,
    text    TEXT,
    quality TEXT NOT NULL DEFAULT 'good'
);
CREATE INDEX IF NOT EXISTS idx_readings_lookup ON readings (device, point, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts);
"""


class History:
    def __init__(self, path: str = DEFAULT_DB) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.commit()

    # ── tulis ─────────────────────────────────────────────────
    def append(self, readings) -> int:
        rows = []
        for r in readings:
            value, text = None, None
            if isinstance(r.value, bool):
                value = 1.0 if r.value else 0.0
            elif isinstance(r.value, (int, float)):
                value = float(r.value)
            elif r.value is not None:
                text = str(r.value)
            rows.append((r.ts, r.device, r.point, value, text, r.quality))
        if not rows:
            return 0
        with self._lock:
            self._db.executemany(
                "INSERT INTO readings (ts, device, point, value, text, quality)"
                " VALUES (?,?,?,?,?,?)", rows)
            self._db.commit()
        return len(rows)

    # ── baca ──────────────────────────────────────────────────
    def query(self, device: str, point: str, since: float | None = None,
              until: float | None = None, limit: int = 500,
              newest_first: bool = True) -> list[dict]:
        sql = "SELECT ts, value, text, quality FROM readings WHERE device=? AND point=?"
        args: list = [device, point]
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        if until is not None:
            sql += " AND ts <= ?"
            args.append(until)
        sql += f" ORDER BY ts {'DESC' if newest_first else 'ASC'} LIMIT ?"
        args.append(max(1, min(limit, 20000)))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [{"ts": r["ts"], "value": r["value"] if r["text"] is None else r["text"],
                 "quality": r["quality"]} for r in rows]

    def stats(self, device: str, point: str, since: float | None = None) -> dict:
        """Ringkasan angka: jumlah, min, max, rata-rata, simpangan, tren per menit."""
        rows = self.query(device, point, since=since, limit=20000, newest_first=False)
        nums = [(r["ts"], r["value"]) for r in rows
                if isinstance(r["value"], (int, float)) and r["quality"] == "good"]
        out = {"device": device, "point": point, "samples": len(rows),
               "numeric_samples": len(nums), "bad_samples":
                   sum(1 for r in rows if r["quality"] != "good")}
        if not nums:
            return out
        values = [v for _, v in nums]
        out.update({
            "first_ts": nums[0][0], "last_ts": nums[-1][0],
            "span_seconds": round(nums[-1][0] - nums[0][0], 2),
            "min": min(values), "max": max(values),
            "mean": round(statistics.fmean(values), 6),
            "median": round(statistics.median(values), 6),
            "stdev": round(statistics.pstdev(values), 6) if len(values) > 1 else 0.0,
            "first": values[0], "last": values[-1],
        })
        out["trend_per_minute"] = round(_slope(nums) * 60, 6)
        out["direction"] = ("naik" if out["trend_per_minute"] > 1e-9 else
                            "turun" if out["trend_per_minute"] < -1e-9 else "datar")
        return out

    def points(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT device, point, COUNT(*) n, MIN(ts) t0, MAX(ts) t1"
                " FROM readings GROUP BY device, point ORDER BY device, point"
            ).fetchall()
        return [{"device": r["device"], "point": r["point"], "samples": r["n"],
                 "first_ts": r["t0"], "last_ts": r["t1"]} for r in rows]

    def export_csv(self, path: str, device: str, point: str | None = None,
                   since: float | None = None) -> int:
        sql = "SELECT ts, device, point, value, text, quality FROM readings WHERE device=?"
        args: list = [device]
        if point:
            sql += " AND point=?"
            args.append(point)
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["waktu_iso", "epoch", "device", "point", "value", "quality"])
            for r in rows:
                w.writerow([
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
                    round(r["ts"], 3), r["device"], r["point"],
                    r["value"] if r["text"] is None else r["text"], r["quality"]])
        return len(rows)

    def prune(self, older_than_days: float) -> int:
        cutoff = time.time() - older_than_days * 86400
        with self._lock:
            cur = self._db.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
            self._db.commit()
        return cur.rowcount

    def clear(self, device: str | None = None) -> int:
        with self._lock:
            cur = (self._db.execute("DELETE FROM readings WHERE device=?", (device,))
                   if device else self._db.execute("DELETE FROM readings"))
            self._db.commit()
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _slope(points: list[tuple[float, float]]) -> float:
    """Kemiringan regresi linier sederhana (satuan nilai per detik)."""
    n = len(points)
    if n < 2:
        return 0.0
    t0 = points[0][0]
    xs = [t - t0 for t, _ in points]
    ys = [v for _, v in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
