import os
import json
import secrets
import string
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_BOT_TOKEN = "8983062859:AAFUVGuT0za1H9TQu0p-k_8Ri-IwxD5ebMs"
ADMIN_ID = 1612210913

USERS_FILE = "users_db.json"
KEYS_FILE = "keys_db.json"
SETTINGS_FILE = "settings_db.json"

# --- Storage Helpers ---
def load_data(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(file_path, data):
    try:
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving {file_path}: {e}")

def check_user_access(user_id):
    if user_id == ADMIN_ID:
        return True, "Admin (Lifetime Access)"
    
    users = load_data(USERS_FILE)
    uid_str = str(user_id)
    if uid_str not in users:
        return False, None
    
    expiry_str = users[uid_str].get("expiry")
    if not expiry_str:
        return False, None
    
    try:
        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False, None

    now = datetime.now()
    if now > expiry_date:
        return False, None
    
    diff = expiry_date - now
    days_left = diff.days
    hours_left = int(diff.seconds / 3600)
    return True, f"{days_left}d {hours_left}h remaining"

# Dummy Web Server for Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive and healthy!")

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

ALIAS_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "^NSEMDCP50",
    "SENSEX": "^BSESN",
    "BANKEX": "^BSEBK",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "CRUDE": "CL=F",
    "CRUDEOIL": "CL=F",
    "NATURALGAS": "NG=F",
    "COPPER": "HG=F",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "USDINR": "USDINR=X",
    "GOLDBEES": "GOLDBEES.NS",
    "SILVERBEES": "SILVERBEES.NS"
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def calculate_op_levels(df):
    dop = float(df['Open'].iloc[-1])
    dc = float(df['Close'].iloc[-2])
    dhigh = float(df['High'].iloc[-2])
    dlow = float(df['Low'].iloc[-2])

    closes = df['Close'].iloc[-12:-1].values
    log_returns = [np.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    avg_logg0 = sum(log_returns) / 10.0
    sq_sum0 = sum([x**2 for x in log_returns]) / 10.0
    volatility0 = np.sqrt(max(0, sq_sum0 - (avg_logg0**2)))
    range11 = round(dc * volatility0)

    if abs(dop - dc) > (0.382 * range11):
        doKdc = dop
    else:
        doKdc = dc

    logg = np.log(doKdc / closes[-2])
    adjusted_logs = [logg] + log_returns[:-1]
    avg_logg = sum(adjusted_logs) / 10.0
    sq_sum = sum([x**2 for x in adjusted_logs]) / 10.0
    volatility = np.sqrt(max(0, sq_sum - (avg_logg**2)))
    range1 = round(doKdc * volatility)

    pivot = round((dhigh + dlow + dc) / 3)
    bc = round((dhigh + dlow) / 2)
    tc = round((pivot - bc) + pivot)

    targets = {}
    ratios = [0.500, 0.618, 0.786, 1.000, 1.272, 1.618, 2.000, 2.272, 2.618, 3.000]
    for idx, r in enumerate(ratios, 1):
        targets[f"bt{idx}"] = round(doKdc + (range1 * r))
        targets[f"st{idx}"] = round(doKdc - (range1 * r))

    return {
        "range": range1, "pivot": pivot, "tc": tc, "bc": bc,
        "b_above": round(doKdc + (range1 * 0.236)),
        "b_conf": round(doKdc + (range1 * 0.382)),
        "s_below": round(doKdc - (range1 * 0.236)),
        "s_conf": round(doKdc - (range1 * 0.382)),
        **targets
    }

def get_mcx_parity(symbol_name):
    target_ticker = "GC=F" if symbol_name == "GOLDMCX" else "SI=F"
    df_com = yf.Ticker(target_ticker).history(period="1mo", interval="1d")
    df_fx = yf.Ticker("USDINR=X").history(period="1mo", interval="1d")

    if df_com.empty or df_fx.empty:
        return None

    fx = float(df_fx['Close'].iloc[-1])
    multiplier = (10.0 / 31.1035) * fx * 1.06 if symbol_name == "GOLDMCX" else (1000.0 / 31.1035) * fx * 1.06

    res = pd.DataFrame(index=df_com.index)
    res['Open'] = df_com['Open'] * multiplier
    res['High'] = df_com['High'] * multiplier
    res['Low'] = df_com['Low'] * multiplier
    res['Close'] = df_com['Close'] * multiplier
    return res

def fetch_data(symbol):
    sym_u = symbol.upper()
    if sym_u in ["GOLDMCX", "MCXGOLD"]:
        df = get_mcx_parity("GOLDMCX")
        return df, "MCX GOLD (10g / INR)"
    elif sym_u in ["SILVERMCX", "MCXSILVER"]:
        df = get_mcx_parity("SILVERMCX")
        return df, "MCX SILVER (1kg / INR)"

    resolved = ALIAS_MAP.get(sym_u, sym_u)
    candidates = [resolved]
    if not any(resolved.endswith(x) for x in [".NS", ".BO", "=F", "=X", "-USD"]) and not resolved.startswith("^"):
        candidates.extend([f"{resolved}.NS", f"{resolved}.BO", f"{resolved}-USD"])

    for s in candidates:
        try:
            df = yf.Ticker(s).history(period="3mo", interval="1d")
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna()
            if len(df) >= 12:
                return df, s
        except Exception:
            continue
    return None, None

def format_color_display(matched_symbol, today_str, lvl):
    out = []
    out.append(f"📊 <b>{matched_symbol} • OP LEVELS</b>")
    out.append(f"📅 <code>{today_str}</code>\n")

    out.append("🔵 <b>CPR / PIVOT RANGE</b>")
    out.append("<code>" + "-"*30 + "</code>")
    out.append(f"  TC (Top Pivot)    : <b>{lvl['tc']}</b>")
    out.append(f"  P  (Central Pivot): <b>{lvl['pivot']}</b>")
    out.append(f"  BC (Bottom Pivot) : <b>{lvl['bc']}</b>")
    out.append(f"  Expected Range    : <b>{lvl['range']}</b>\n")

    out.append("🟢 <b>BUYING ZONE (BULLISH)</b>")
    out.append("<code>" + "-"*30 + "</code>")
    out.append(f"  Buy Above         : <b>{lvl['b_above']}</b>")
    out.append(f"  Buy Confirm       : <b>{lvl['b_conf']}</b>")
    for i in range(1, 11):
        out.append(f"  Target {i:<2}         : <b>{lvl[f'bt{i}']}</b>")
    out.append("")

    out.append("🟠 <b>NO TRADE ZONE (RANGEBOUND / CHOPPY)</b>")
    out.append("<code>" + "-"*30 + "</code>")
    out.append(f"  Zone Range        : <b>{lvl['s_conf']}  ⇄  {lvl['b_conf']}</b>")
    out.append("  <i>(Strictly avoid new intraday positions here)</i>\n")

    out.append("🔴 <b>SELLING ZONE (BEARISH)</b>")
    out.append("<code>" + "-"*30 + "</code>")
    out.append(f"  Sell Below        : <b>{lvl['s_below']}</b>")
    out.append(f"  Sell Confirm      : <b>{lvl['s_conf']}</b>")
    for i in range(1, 11):
        out.append(f"  Target {i:<2}         : <b>{lvl[f'st{i}']}</b>")

    return "\n".join(out)

# --- GramAds / AdGram Integration ---
def get_ad_markup(user_id):
    settings = load_data(SETTINGS_FILE)
    ad_token = settings.get("gramads_token")
    if not ad_token:
        return None
    
    try:
        url = f"https://api.adgram.io/adv?token={ad_token}&user_id={user_id}"
        resp = requests.get(url, timeout=3).json()
        if resp.get("status") == "success" and "button" in resp:
            title = resp["button"].get("text", "📢 Sponsored Channel")
            link = resp["button"].get("url")
            return InlineKeyboardMarkup([[InlineKeyboardButton(title, url=link)]])
    except Exception:
        pass
    return None

# --- Admin Commands ---

async def setgramads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Format: <code>/setgramads &lt;TOKEN&gt;</code>", parse_mode="HTML")
        return
    token = context.args[0].strip()
    settings = load_data(SETTINGS_FILE)
    settings["gramads_token"] = token
    save_data(SETTINGS_FILE, settings)
    await update.message.reply_text("✅ <b>GramAds Token Saved!</b> Live ads enabled.", parse_mode="HTML")

async def genkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Format: <code>/genkey &lt;days&gt;</code>", parse_mode="HTML")
        return
    try:
        days = int(context.args[0])
    except ValueError:
        return
    random_str = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    key_code = f"OP-{days}D-{random_str}"
    keys = load_data(KEYS_FILE)
    keys[key_code] = {"days": days, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    save_data(KEYS_FILE, keys)
    await update.message.reply_text(f"🎟️ Key: <code>{key_code}</code>\nValidity: {days} Din\n\nForward to user:\n<code>/redeem {key_code}</code>", parse_mode="HTML")

async def delkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args:
        return
    keys = load_data(KEYS_FILE)
    k = context.args[0].strip().upper()
    if k in keys:
        del keys[k]
        save_data(KEYS_FILE, keys)
        await update.message.reply_text(f"🗑️ Deleted: <code>{k}</code>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Key nahi mili.")

async def deluser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args:
        return
    users = load_data(USERS_FILE)
    uid = context.args[0].strip()
    if uid in users:
        del users[uid]
        save_data(USERS_FILE, users)
        await update.message.reply_text(f"🚫 Revoked user: <code>{uid}</code>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ User ID nahi mila.")

async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_data(USERS_FILE)
    lines = ["👥 <b>Subscribers:</b>\n"]
    for uid, d in users.items():
        lines.append(f"• @{d.get('username')} (<code>{uid}</code>) -> {d.get('expiry')}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def keys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    keys = load_data(KEYS_FILE)
    lines = ["🎟️ <b>Unredeemed Keys:</b>\n"]
    for k, v in keys.items():
        lines.append(f"• <code>{k}</code> ({v.get('days')}D)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# --- User Handlers ---

async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Format: <code>/redeem KEY</code>")
        return
    k = context.args[0].strip().upper()
    keys = load_data(KEYS_FILE)
    if k not in keys:
        await update.message.reply_text("❌ Galat ya used key.")
        return
    days = keys[k]["days"]
    users = load_data(USERS_FILE)
    now = datetime.now()
    new_expiry = now + timedelta(days=days)
    users[uid] = {"expiry": new_expiry.strftime("%Y-%m-%d %H:%M:%S"), "username": update.effective_user.username or "N/A"}
    save_data(USERS_FILE, users)
    del keys[k]
    save_data(KEYS_FILE, keys)
    await update.message.reply_text(f"🎉 Activated {days} Din! Expiry: {new_expiry.strftime('%Y-%m-%d')}")

async def myplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    has_access, info = check_user_access(uid)
    if has_access:
        await update.message.reply_text(f"✅ <b>Status:</b> Active\n⏳ Validity: <code>{info}</code>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ <b>Inactive</b>\nRedeem karne ke liye: <code>/redeem KEY</code>", parse_mode="HTML")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    has_access, info = check_user_access(uid)
    if not has_access:
        await update.message.reply_text("🔒 Bot is locked.\nRedeem key: <code>/redeem KEY</code>", parse_mode="HTML")
        return
    await update.message.reply_text(f"👋 Active ({info})\nCommands: GOLDMCX, SILVERMCX, NIFTY, BANKNIFTY\nValidity check: /myplan")

async def process_ticker(update: Update, query: str):
    uid = update.effective_user.id
    has_access, info = check_user_access(uid)
    if not has_access:
        await update.message.reply_text("🔒 Subscription expired. Key redeem karein.")
        return

    await update.message.reply_text(f"⏳ Calculating OP levels for <b>{query.upper()}</b>...", parse_mode="HTML")
    df, matched_symbol = fetch_data(query)
    if df is None:
        await update.message.reply_text("❌ Symbol not found.")
        return

    lvl = calculate_op_levels(df)
    ist = pytz.timezone('Asia/Kolkata')
    today_str = datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST')
    formatted_msg = format_color_display(matched_symbol, today_str, lvl)
    
    # GramAds inline button
    reply_markup = get_ad_markup(uid)
    await update.message.reply_text(formatted_msg, parse_mode="HTML", reply_markup=reply_markup)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query.startswith("/"):
        await process_ticker(update, query)

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("myplan", myplan_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("genkey", genkey_cmd))
    app.add_handler(CommandHandler("delkey", delkey_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("keys", keys_cmd))
    app.add_handler(CommandHandler("deluser", deluser_cmd))
    app.add_handler(CommandHandler("setgramads", setgramads_cmd))
    
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))
    print("Bot is polling live...")
    app.run_polling()

if __name__ == "__main__":
    main()
