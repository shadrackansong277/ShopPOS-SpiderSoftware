"""
ShopPOS Cloud Relay API
========================
The hosted counterpart to the sync agent already built into ShopPOS
(python_api/remote/sync_agent.py) and its Settings > Remote Access page
(renderer/js/pages/remote.js). Deploy this anywhere (Render, Railway,
Fly.io, a VPS) and point a shop's Remote Access settings at its URL.

Endpoints the desktop app already calls (unchanged, contract matched
exactly - no changes needed on the ShopPOS Electron side):

    POST /api/register   - one-time shop signup, returns shop_id
    POST /api/push        - sync agent pushes sales/stock/snapshot every 30s
                            (auth: X-Shop-Key header)

Endpoints this relay adds for the owner:

    GET  /login, POST /login     - owner signs in with the email/password
                                    used at registration
    GET  /logout
    GET  /dashboard              - live web dashboard (session-protected)
    GET  /api/dashboard/data     - JSON the dashboard polls
    GET  /api/health             - uptime check
"""

import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, render_template, redirect, url_for

import db

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") != "development"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)

if not os.environ.get("SECRET_KEY"):
    print("[warn] SECRET_KEY not set - using a random key that resets on every "
          "restart, which will log everyone out. Set SECRET_KEY in your host's "
          "environment variables.")

db.init_db()


# ── Helpers ──────────────────────────────────────────────────────────────
def ok(**data):
    return jsonify({"ok": True, **data})


def err(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def current_shop():
    shop_id = session.get("shop_id")
    if not shop_id:
        return None
    return db.query_one("SELECT * FROM shops WHERE id=?", (shop_id,))


def require_login():
    shop = current_shop()
    if not shop:
        return None
    return shop


# ── Registration (called once from ShopPOS Settings > Remote Access) ───────
@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.get_json(silent=True) or {}
    shop_name = (d.get("shop_name") or "My Shop").strip()
    admin_email = (d.get("admin_email") or "").strip().lower()
    admin_password_hash = d.get("admin_password") or ""   # already sha256'd by the client
    shop_key = d.get("shop_key") or ""

    if not admin_email:
        return err("admin_email is required")
    if not admin_password_hash:
        return err("admin_password is required")
    if not shop_key:
        return err("shop_key is required")

    existing = db.query_one("SELECT id FROM shops WHERE admin_email=?", (admin_email,))
    if existing:
        # Re-registration from the same owner email updates the existing shop
        # (this is how the desktop app's "Update Settings" button behaves).
        shop_id = existing["id"]
        db.execute(
            "UPDATE shops SET shop_name=?, admin_password_hash=?, shop_key_hash=? WHERE id=?",
            (shop_name, admin_password_hash, sha256(shop_key), shop_id),
        )
        return ok(shop_id=shop_id, message="Shop settings updated")

    shop_id = secrets.token_hex(12)
    db.execute(
        """INSERT INTO shops (id, shop_name, admin_email, admin_password_hash, shop_key_hash)
           VALUES (?, ?, ?, ?, ?)""",
        (shop_id, shop_name, admin_email, admin_password_hash, sha256(shop_key)),
    )
    return ok(shop_id=shop_id, message="Shop registered")


# ── Push ingestion (called every 30s by remote/sync_agent.py) ──────────────
@app.route("/api/push", methods=["POST"])
def api_push():
    shop_key = request.headers.get("X-Shop-Key", "")
    d = request.get_json(silent=True) or {}
    shop_id = d.get("shop_id") or ""

    if not shop_id or not shop_key:
        return err("shop_id and X-Shop-Key header are required", 401)

    shop = db.query_one("SELECT * FROM shops WHERE id=?", (shop_id,))
    if not shop or shop["shop_key_hash"] != sha256(shop_key):
        return err("Invalid shop_id or shop_key", 401)

    sales = d.get("sales") or []
    movements = d.get("movements") or []
    low_stock = d.get("low_stock") or []
    snapshot = d.get("snapshot") or {}

    for s in sales:
        db.execute(
            """INSERT INTO sales
                 (shop_id, remote_sale_id, invoice_number, customer_name, cashier,
                  subtotal, discount, total, amount_paid, change_given,
                  payment_method, status, notes, items_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (shop_id, remote_sale_id) DO UPDATE SET
                  status=excluded.status, notes=excluded.notes
            """ if db.USE_POSTGRES else
            """INSERT INTO sales
                 (shop_id, remote_sale_id, invoice_number, customer_name, cashier,
                  subtotal, discount, total, amount_paid, change_given,
                  payment_method, status, notes, items_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (shop_id, remote_sale_id) DO UPDATE SET
                  status=excluded.status, notes=excluded.notes
            """,
            (shop_id, s.get("id"), s.get("invoice_number"), s.get("customer_name"),
             s.get("cashier"), s.get("subtotal"), s.get("discount"), s.get("total"),
             s.get("amount_paid"), s.get("change_given"), s.get("payment_method"),
             s.get("status"), s.get("notes"), json.dumps(s.get("items") or []),
             s.get("created_at")),
        )

    for m in movements:
        db.execute(
            """INSERT INTO movements
                 (shop_id, remote_move_id, product_name, movement_type, quantity,
                  reference, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT (shop_id, remote_move_id) DO NOTHING""",
            (shop_id, m.get("id"), m.get("product_name"), m.get("movement_type"),
             m.get("quantity"), m.get("reference"), m.get("notes"), m.get("created_at")),
        )

    db.execute(
        "UPDATE shops SET low_stock_json=?, low_stock_count=?, last_seen=? WHERE id=?",
        (json.dumps(low_stock), len(low_stock), datetime.utcnow().isoformat(), shop_id),
    )

    if snapshot.get("date"):
        by_payment = json.dumps(snapshot.get("by_payment") or [])
        by_cashier = json.dumps(snapshot.get("by_cashier") or [])
        params = (
            shop_id, snapshot["date"],
            snapshot.get("total_revenue", 0), snapshot.get("num_sales", 0),
            snapshot.get("total_discounts", 0), snapshot.get("total_collected", 0),
            by_payment, by_cashier, snapshot.get("low_stock_count", 0),
        )
        if db.USE_POSTGRES:
            db.execute(
                """INSERT INTO snapshots
                     (shop_id, date, total_revenue, num_sales, total_discounts,
                      total_collected, by_payment_json, by_cashier_json, low_stock_count)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (shop_id, date) DO UPDATE SET
                     total_revenue=excluded.total_revenue, num_sales=excluded.num_sales,
                     total_discounts=excluded.total_discounts, total_collected=excluded.total_collected,
                     by_payment_json=excluded.by_payment_json, by_cashier_json=excluded.by_cashier_json,
                     low_stock_count=excluded.low_stock_count, updated_at=now()""",
                params,
            )
        else:
            db.execute(
                """INSERT INTO snapshots
                     (shop_id, date, total_revenue, num_sales, total_discounts,
                      total_collected, by_payment_json, by_cashier_json, low_stock_count)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (shop_id, date) DO UPDATE SET
                     total_revenue=excluded.total_revenue, num_sales=excluded.num_sales,
                     total_discounts=excluded.total_discounts, total_collected=excluded.total_collected,
                     by_payment_json=excluded.by_payment_json, by_cashier_json=excluded.by_cashier_json,
                     low_stock_count=excluded.low_stock_count, updated_at=datetime('now')""",
                params,
            )

    return ok(received={"sales": len(sales), "movements": len(movements)})


# ── Owner login / dashboard ─────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("dashboard") if current_shop() else url_for("login"))


@app.route("/login", methods=["GET"])
def login():
    if current_shop():
        return redirect(url_for("dashboard"))
    return render_template("login.html", error=None)


@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json(silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    password_hash = d.get("password_hash") or ""  # sha256'd client-side in login.html

    shop = db.query_one("SELECT * FROM shops WHERE admin_email=?", (email,))
    if not shop or shop["admin_password_hash"] != password_hash:
        return err("Incorrect email or password", 401)

    session.permanent = True
    session["shop_id"] = shop["id"]
    return ok()


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    shop = require_login()
    if not shop:
        return redirect(url_for("login"))
    return render_template("dashboard.html", shop_name=shop["shop_name"])


@app.route("/api/dashboard/data")
def api_dashboard_data():
    shop = require_login()
    if not shop:
        return err("Not authenticated", 401)

    shop_id = shop["id"]
    today = datetime.utcnow().strftime("%Y-%m-%d")

    today_snap = db.query_one("SELECT * FROM snapshots WHERE shop_id=? AND date=?", (shop_id, today))

    since = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    trend_rows = db.query_all(
        "SELECT date, total_revenue, num_sales FROM snapshots WHERE shop_id=? AND date>=? ORDER BY date",
        (shop_id, since),
    )

    recent = db.query_all(
        """SELECT invoice_number, customer_name, cashier, total, payment_method,
                  status, created_at
           FROM sales WHERE shop_id=? ORDER BY received_at DESC LIMIT 50""",
        (shop_id,),
    )

    low_stock = json.loads(shop["low_stock_json"] or "[]")

    return ok(
        shop_name=shop["shop_name"],
        last_seen=shop["last_seen"],
        today={
            "revenue": (today_snap or {}).get("total_revenue", 0),
            "num_sales": (today_snap or {}).get("num_sales", 0),
            "discounts": (today_snap or {}).get("total_discounts", 0),
            "by_payment": json.loads((today_snap or {}).get("by_payment_json") or "[]"),
            "by_cashier": json.loads((today_snap or {}).get("by_cashier_json") or "[]"),
        },
        trend=trend_rows,
        recent_sales=recent,
        low_stock=low_stock,
        low_stock_count=shop["low_stock_count"] or 0,
    )


@app.route("/api/health")
def health():
    return ok(status="up", backend="postgres" if db.USE_POSTGRES else "sqlite")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
