"""CRM заправок газгольдеров: FastAPI + SQLite. Swagger: /docs"""
import csv, io, os, sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

DB_PATH = os.getenv("DB_PATH", "crm.db")
API_KEY = os.getenv("API_KEY", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
STATUSES = ("new", "scheduled", "done", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS referrers(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, reward_default REAL DEFAULT 0,
  note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, address TEXT,
  referrer_id INTEGER REFERENCES referrers(id), note TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS tanks(
  id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id),
  title TEXT, volume_l REAL, brand TEXT, serial TEXT, address TEXT,
  interval_days INTEGER, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS refills(
  id INTEGER PRIMARY KEY,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  tank_id INTEGER REFERENCES tanks(id),
  referrer_id INTEGER REFERENCES referrers(id),
  status TEXT NOT NULL DEFAULT 'new',
  scheduled_date TEXT, done_date TEXT,
  volume_l REAL, price_per_liter REAL, total_price REAL,
  cost_per_liter REAL, referrer_reward REAL DEFAULT 0, paid INTEGER DEFAULT 0,
  interval_days INTEGER, next_refill_date TEXT,
  note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_refills_client ON refills(client_id);
CREATE INDEX IF NOT EXISTS ix_refills_next ON refills(next_refill_date);
"""

# поля каждой сущности (без id/created_at)
FIELDS = {
    "referrers": ["name", "phone", "reward_default", "note"],
    "clients": ["name", "phone", "address", "referrer_id", "note"],
    "tanks": ["client_id", "title", "volume_l", "brand", "serial", "address", "interval_days", "note"],
    "refills": ["client_id", "tank_id", "referrer_id", "status", "scheduled_date", "done_date",
                "volume_l", "price_per_liter", "total_price", "cost_per_liter", "referrer_reward",
                "paid", "interval_days", "next_refill_date", "note"],
}


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def auth(x_api_key: str = Header(default=""), api_key: str = Query(default="")):
    if API_KEY and API_KEY not in (x_api_key, api_key):
        raise HTTPException(401, "bad api key")


app = FastAPI(title="Gas CRM")
api = APIRouter(prefix="/api", dependencies=[Depends(auth)])
with db() as c:
    c.executescript(SCHEMA)


# ---------- модели ----------
class Referrer(BaseModel):
    name: str
    phone: Optional[str] = None
    reward_default: float = 0
    note: Optional[str] = None


class Client(BaseModel):
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    referrer_id: Optional[int] = None
    note: Optional[str] = None


class Tank(BaseModel):
    client_id: int
    title: Optional[str] = None
    volume_l: Optional[float] = None
    brand: Optional[str] = None
    serial: Optional[str] = None
    address: Optional[str] = None
    interval_days: Optional[int] = None  # через сколько дней обычно нужна заправка
    note: Optional[str] = None


class Refill(BaseModel):
    client_id: int
    tank_id: Optional[int] = None
    referrer_id: Optional[int] = None  # по умолчанию берётся из клиента
    status: str = "new"
    scheduled_date: Optional[str] = None  # YYYY-MM-DD
    done_date: Optional[str] = None
    volume_l: Optional[float] = None
    price_per_liter: Optional[float] = None
    total_price: Optional[float] = None  # если не задано = volume_l * price_per_liter
    cost_per_liter: Optional[float] = None  # себестоимость, для маржи
    referrer_reward: Optional[float] = None  # по умолчанию reward_default привёдшего
    paid: bool = False
    interval_days: Optional[int] = None  # по умолчанию из газгольдера
    next_refill_date: Optional[str] = None  # по умолчанию done_date + interval_days
    note: Optional[str] = None


MODELS = {"referrers": Referrer, "clients": Client, "tanks": Tank, "refills": Refill}


# ---------- вспомогательное ----------
def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(con, table, id_):
    r = con.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
    if not r:
        raise HTTPException(404, f"{table}/{id_} not found")
    return dict(r)


def derive_refill(con, d: dict) -> dict:
    """Автозаполнение: привёдший, награда, интервал, сумма, дата следующей заправки."""
    if d["status"] not in STATUSES:
        raise HTTPException(422, f"status must be one of {STATUSES}")
    client = one(con, "clients", d["client_id"])
    if d.get("tank_id"):
        tank = one(con, "tanks", d["tank_id"])
        if tank["client_id"] != d["client_id"]:
            raise HTTPException(422, "tank belongs to another client")
        if d.get("interval_days") is None:
            d["interval_days"] = tank["interval_days"]
    if d.get("referrer_id") is None:
        d["referrer_id"] = client["referrer_id"]
    if d.get("referrer_reward") is None:
        ref = con.execute("SELECT reward_default FROM referrers WHERE id=?", (d["referrer_id"],)).fetchone()
        d["referrer_reward"] = ref["reward_default"] if ref else 0
    if d.get("total_price") is None and d.get("volume_l") and d.get("price_per_liter"):
        d["total_price"] = round(d["volume_l"] * d["price_per_liter"], 2)
    if d["status"] == "done" and not d.get("done_date"):
        d["done_date"] = date.today().isoformat()
    if not d.get("next_refill_date") and d.get("done_date") and d.get("interval_days"):
        d["next_refill_date"] = (date.fromisoformat(d["done_date"]) + timedelta(days=d["interval_days"])).isoformat()
    d["paid"] = int(bool(d.get("paid")))
    return d


async def send_webhook(event: str, data: dict):
    if not WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as h:
            await h.post(WEBHOOK_URL, json={"event": event, "data": data})
    except Exception:
        pass


REFILL_VIEW = """
SELECT r.*, c.name AS client_name, c.phone AS client_phone, c.address AS client_address,
  t.title AS tank_title, t.volume_l AS tank_volume_l, rf.name AS referrer_name,
  CASE WHEN r.total_price IS NOT NULL AND r.cost_per_liter IS NOT NULL AND r.volume_l IS NOT NULL
       THEN ROUND(r.total_price - r.cost_per_liter*r.volume_l - COALESCE(r.referrer_reward,0), 2) END AS profit
FROM refills r
JOIN clients c ON c.id=r.client_id
LEFT JOIN tanks t ON t.id=r.tank_id
LEFT JOIN referrers rf ON rf.id=r.referrer_id
"""


def refill_full(con, id_):
    r = con.execute(REFILL_VIEW + " WHERE r.id=?", (id_,)).fetchone()
    if not r:
        raise HTTPException(404, "refill not found")
    return dict(r)


# ---------- заявки ----------
@api.get("/refills")
def list_refills(status: Optional[str] = None, client_id: Optional[int] = None,
                 referrer_id: Optional[int] = None, date_from: Optional[str] = None,
                 date_to: Optional[str] = None, q: Optional[str] = None, limit: int = 500):
    where, args = [], []
    for col, v in (("r.status", status), ("r.client_id", client_id), ("r.referrer_id", referrer_id)):
        if v is not None:
            where.append(f"{col}=?"); args.append(v)
    if date_from:
        where.append("COALESCE(r.done_date, r.scheduled_date, substr(r.created_at,1,10))>=?"); args.append(date_from)
    if date_to:
        where.append("COALESCE(r.done_date, r.scheduled_date, substr(r.created_at,1,10))<=?"); args.append(date_to)
    if q:
        where.append("(c.name LIKE ? OR c.phone LIKE ? OR r.note LIKE ?)"); args += [f"%{q}%"] * 3
    sql = REFILL_VIEW + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY r.id DESC LIMIT ?"
    with db() as con:
        return rows(con, sql, (*args, limit))


@api.post("/refills", status_code=201)
def create_refill(body: Refill, bg: BackgroundTasks):
    with db() as con:
        d = derive_refill(con, body.model_dump())
        cols = FIELDS["refills"]
        cur = con.execute(f"INSERT INTO refills({','.join(cols)}) VALUES({','.join('?'*len(cols))})",
                          [d.get(k) for k in cols])
        out = refill_full(con, cur.lastrowid)
    bg.add_task(send_webhook, "refill.created", out)
    return out


@api.get("/refills/{id}")
def get_refill(id: int):
    with db() as con:
        return refill_full(con, id)


@api.patch("/refills/{id}")
def patch_refill(id: int, body: dict, bg: BackgroundTasks):
    with db() as con:
        old = one(con, "refills", id)
        prev_status = old["status"]
        # производные поля пересчитываются, если их не задали явно
        if "total_price" not in body and {"volume_l", "price_per_liter"} & body.keys():
            old["total_price"] = None
        if "next_refill_date" not in body and {"done_date", "interval_days", "status"} & body.keys():
            old["next_refill_date"] = None
        old.update({k: v for k, v in body.items() if k in FIELDS["refills"]})
        d = derive_refill(con, Refill(**{k: old[k] for k in FIELDS["refills"]}).model_dump())
        sets = ",".join(f"{k}=?" for k in FIELDS["refills"])
        con.execute(f"UPDATE refills SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [d.get(k) for k in FIELDS["refills"]] + [id])
        out = refill_full(con, id)
    bg.add_task(send_webhook, "refill.done" if out["status"] == "done" and prev_status != "done" else "refill.updated", out)
    return out


@api.delete("/refills/{id}", status_code=204)
def delete_refill(id: int):
    with db() as con:
        one(con, "refills", id)
        con.execute("DELETE FROM refills WHERE id=?", (id,))


# ---------- напоминания / аналитика / экспорт ----------
@api.get("/due")
def due(days: int = 14):
    """Клиенты, которым пора заправляться: последняя выполненная заправка по газгольдеру,
    next_refill_date <= сегодня+days (просроченные тоже). Для n8n: дёргать раз в день."""
    limit = (date.today() + timedelta(days=days)).isoformat()
    sql = """SELECT * FROM (
      SELECT r.id AS refill_id, r.client_id, c.name AS client_name, c.phone AS client_phone,
             r.tank_id, t.title AS tank_title, r.done_date AS last_done_date, r.next_refill_date,
             CAST(julianday(r.next_refill_date) - julianday('now') AS INTEGER) AS days_left,
             r.price_per_liter AS last_price_per_liter, rf.name AS referrer_name,
             ROW_NUMBER() OVER (PARTITION BY r.client_id, COALESCE(r.tank_id,0) ORDER BY r.done_date DESC, r.id DESC) AS rn
      FROM refills r JOIN clients c ON c.id=r.client_id
      LEFT JOIN tanks t ON t.id=r.tank_id LEFT JOIN referrers rf ON rf.id=r.referrer_id
      WHERE r.status='done') WHERE rn=1 AND next_refill_date IS NOT NULL AND next_refill_date<=?
      ORDER BY next_refill_date"""
    with db() as con:
        return rows(con, sql, (limit,))


@api.get("/stats")
def stats(date_from: Optional[str] = None, date_to: Optional[str] = None):
    w, a = "WHERE r.status='done'", []
    if date_from: w += " AND r.done_date>=?"; a.append(date_from)
    if date_to: w += " AND r.done_date<=?"; a.append(date_to)
    with db() as con:
        total = rows(con, f"""SELECT COUNT(*) AS refills, ROUND(SUM(total_price),2) AS revenue,
            ROUND(SUM(volume_l),1) AS liters, ROUND(SUM(referrer_reward),2) AS referrer_rewards,
            ROUND(SUM(total_price - COALESCE(cost_per_liter*volume_l,0) - COALESCE(referrer_reward,0)),2) AS profit
            FROM refills r {w}""", a)[0]
        by_ref = rows(con, f"""SELECT COALESCE(rf.name,'— без привёдшего —') AS referrer, COUNT(*) AS refills,
            ROUND(SUM(r.total_price),2) AS revenue, ROUND(SUM(r.referrer_reward),2) AS rewards
            FROM refills r LEFT JOIN referrers rf ON rf.id=r.referrer_id {w} GROUP BY r.referrer_id ORDER BY revenue DESC""", a)
        by_month = rows(con, f"""SELECT substr(done_date,1,7) AS month, COUNT(*) AS refills,
            ROUND(SUM(total_price),2) AS revenue FROM refills r {w} GROUP BY month ORDER BY month""", a)
        unpaid = rows(con, "SELECT COUNT(*) AS n, ROUND(SUM(total_price),2) AS sum FROM refills WHERE status='done' AND paid=0")[0]
        new = rows(con, "SELECT COUNT(*) AS n FROM refills WHERE status IN ('new','scheduled')")[0]["n"]
    return {"total": total, "by_referrer": by_ref, "by_month": by_month, "unpaid": unpaid, "open_requests": new}


@api.get("/export/refills.csv")
def export_csv():
    with db() as con:
        data = rows(con, REFILL_VIEW + " ORDER BY r.id")
    buf = io.StringIO()
    if data:
        w = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
        w.writeheader(); w.writerows(data)
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=refills.csv"})


# ---------- CRUD для справочников ----------
def make_crud(table: str, model):
    @api.get(f"/{table}", name=f"list_{table}")
    def _list(q: Optional[str] = None, client_id: Optional[int] = None):
        sql, args, w = f"SELECT * FROM {table}", [], []
        if q and "name" in FIELDS[table]:
            if "phone" in FIELDS[table]:
                w.append("(name LIKE ? OR phone LIKE ?)"); args += [f"%{q}%"] * 2
            else:
                w.append("name LIKE ?"); args.append(f"%{q}%")
        if client_id is not None and "client_id" in FIELDS[table]:
            w.append("client_id=?"); args.append(client_id)
        if w: sql += " WHERE " + " AND ".join(w)
        with db() as con:
            return rows(con, sql + " ORDER BY id DESC", args)

    @api.post(f"/{table}", status_code=201, name=f"create_{table}")
    def _create(body: model):  # type: ignore[valid-type]
        cols = FIELDS[table]; d = body.model_dump()
        with db() as con:
            try:
                cur = con.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?'*len(cols))})", [d[k] for k in cols])
            except sqlite3.IntegrityError as e:
                raise HTTPException(422, str(e))
            return one(con, table, cur.lastrowid)

    @api.get(f"/{table}/{{id}}", name=f"get_{table}")
    def _get(id: int):
        with db() as con:
            return one(con, table, id)

    @api.patch(f"/{table}/{{id}}", name=f"patch_{table}")
    def _patch(id: int, body: dict):
        with db() as con:
            cur = one(con, table, id)
            cur.update({k: v for k, v in body.items() if k in FIELDS[table]})
            d = model(**{k: cur[k] for k in FIELDS[table]}).model_dump()
            try:
                con.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in FIELDS[table])} WHERE id=?",
                            [d[k] for k in FIELDS[table]] + [id])
            except sqlite3.IntegrityError as e:
                raise HTTPException(422, str(e))
            return one(con, table, id)

    @api.delete(f"/{table}/{{id}}", status_code=204, name=f"delete_{table}")
    def _del(id: int):
        with db() as con:
            one(con, table, id)
            try:
                con.execute(f"DELETE FROM {table} WHERE id=?", (id,))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "есть связанные записи")


for _t in ("referrers", "clients", "tanks"):
    make_crud(_t, MODELS[_t])


app.include_router(api)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))
