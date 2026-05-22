"""Flask + SocketIO dashboard server — with AI Analyst endpoints."""
import threading
import os
from pathlib import Path
from flask import Flask, jsonify, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO
from functools import wraps
from dotenv import load_dotenv

# Load dashboard credentials from parent directory
env_path = Path(__file__).parent.parent / '.env.dashboard'
load_dotenv(env_path)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "mexc_trading_bot_2024")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Login credentials from .env.dashboard
USERNAME = os.getenv("DASHBOARD_USERNAME", "abdullah")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "abdullah")

# Set by main.py after bot is initialized
_trader        = None
_fetcher       = None
_analyst       = None   # AIAnalyst instance
_ai_model      = None   # AIModel instance (for feature importance)
_bot_state     = {"running": False, "paused": False}
_bot_controller = None


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def init_dashboard(trader, fetcher, bot_controller, analyst=None, ai_model=None):
    global _trader, _fetcher, _bot_controller, _analyst, _ai_model
    _trader         = trader
    _fetcher        = fetcher
    _bot_controller = bot_controller
    _analyst        = analyst
    _ai_model       = ai_model
    trader.add_event_callback(_on_trade_event)


def _on_trade_event(event: str, data: dict):
    socketio.emit(event, data)


def emit_ai_insight(insight_dict: dict):
    """Called from main.py background thread to push AI events."""
    socketio.emit("ai_insight", insight_dict)


def emit_ai_regime(regime_dict: dict):
    socketio.emit("ai_regime", regime_dict)


def emit_ai_alert(alert_dict: dict):
    socketio.emit("ai_alert", alert_dict)


# -------------------- AUTH ROUTES --------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == USERNAME and password == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))


# -------------------- CORE ROUTES --------------------

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/prices")
@login_required
def prices():
    return jsonify(_fetcher.get_all_prices() if _fetcher else {})


@app.route("/api/positions")
@login_required
def positions():
    return jsonify(_trader.get_open_positions() if _trader else [])


@app.route("/api/trades")
@login_required
def trades():
    return jsonify(_trader.get_trade_log(50) if _trader else [])


@app.route("/api/stats")
@login_required
def stats():
    return jsonify(_trader.get_stats() if _trader else {})


@app.route("/api/status")
@login_required
def status():
    return jsonify(_bot_state)


@app.route("/api/control/<cmd>", methods=["POST"])
@login_required
def control(cmd):
    global _bot_state
    if _bot_controller:
        _bot_controller(cmd)
    if cmd == "start":
        _bot_state = {"running": True,  "paused": False}
    elif cmd == "stop":
        _bot_state = {"running": False, "paused": False}
    elif cmd == "pause":
        _bot_state["paused"] = not _bot_state.get("paused", False)
    socketio.emit("bot_status", _bot_state)
    return jsonify({"ok": True, "state": _bot_state})


@app.route("/api/control/close_all", methods=["POST"])
@login_required
def close_all():
    if _trader:
        _trader.close_all_positions()
    return jsonify({"ok": True, "message": "Manual close triggered"})


@app.route("/api/control/close/<symbol>", methods=["POST"])
@login_required
def close_one(symbol):
    if _trader:
        ok = _trader.close_position_by_symbol(symbol)
        return jsonify({"ok": ok, "message": f"Close {symbol} {'triggered' if ok else 'failed'}"})
    return jsonify({"ok": False, "message": "Trader not available"})


# -------------------- AI ROUTES --------------------

@app.route("/api/ai/insights")
@login_required
def ai_insights():
    limit = int(request.args.get("limit", 30))
    if _analyst:
        return jsonify(_analyst.get_insights(limit))
    return jsonify([])


@app.route("/api/ai/regime")
def ai_regime():
    if _analyst:
        return jsonify(_analyst.get_regimes())
    return jsonify({})


@app.route("/api/ai/features/<symbol>")
def ai_features(symbol):
    if _ai_model:
        imp = _ai_model.get_feature_importance(symbol)
        # Return top 8 as [{name, score}]
        top = [{"name": n, "score": round(s, 1)} for n, s in imp[:8]]
        return jsonify(top)
    return jsonify([])


@app.route("/api/ai/status")
def ai_status():
    if _analyst:
        return jsonify({
            "gemini_active": _analyst.is_gemini_active(),
            "mode": "Gemini 2.0 Flash" if _analyst.is_gemini_active() else "Rule-Based",
        })
    return jsonify({"gemini_active": False, "mode": "Unavailable"})


@app.route("/api/ai/ask", methods=["POST"])
def ai_ask():
    body     = request.get_json(force=True, silent=True) or {}
    question = body.get("question", "").strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    # Build market context string
    context = ""
    if _fetcher:
        prices = _fetcher.get_all_prices()
        context += "Current prices: " + ", ".join(f"{s}={p:.4f}" for s, p in prices.items()) + "\n"
    if _trader:
        pos = _trader.get_open_positions()
        if pos:
            context += f"Open positions: {len(pos)} - " + ", ".join(p["symbol"] for p in pos) + "\n"
        stats_d = _trader.get_stats()
        if stats_d:
            context += (f"Stats: {stats_d.get('total_trades',0)} trades, "
                        f"WR={stats_d.get('win_rate',0)}%, "
                        f"PnL={stats_d.get('total_pnl',0):.2f} USDT\n")
    if _analyst:
        regimes = _analyst.get_regimes()
        if regimes:
            context += "Regimes: " + ", ".join(f"{s}={r}" for s, r in regimes.items())

    if _analyst:
        answer = _analyst.ask(question, context)
    else:
        answer = "AI Analyst not initialised."

    return jsonify({"answer": answer, "context_used": bool(context)})


# -------------------- SOCKETIO --------------------

@socketio.on("connect")
def on_connect():
    if _fetcher:
        socketio.emit("prices",    _fetcher.get_all_prices())
    if _trader:
        socketio.emit("positions", _trader.get_open_positions())
        socketio.emit("stats",     _trader.get_stats())
    socketio.emit("bot_status", _bot_state)
    if _analyst:
        socketio.emit("ai_insights", _analyst.get_insights(20))
        socketio.emit("ai_regime",   _analyst.get_regimes())


def run_dashboard(host="0.0.0.0", port=5000):
    socketio.run(app, host=host, port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)

