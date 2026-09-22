import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_BOT_TOKEN = "8983062859:AAFUVGuT0za1H9TQu0p-k_8Ri-IwxD5ebMs"

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
    # 1 Troy Oz = 31.1035g | GoldMCX per 10g + 6% duty/premium | SilverMCX per 1kg + 6%
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

def build_table(lvl):
    w1, w2 = 16, 12
    lines = [
        f"┌{'─'*w1}┬{'─'*w2}┐",
        f"│ {'LEVEL TYPE':<{w1-2}} │ {'PRICE':<{w2-2}} │",
        f"├{'─'*w1}┼{'─'*w2}┤",
        f"│ CPR Top (TC)   │ {lvl['tc']:<{w2-2}} │",
        f"│ Pivot (P)      │ {lvl['pivot']:<{w2-2}} │",
        f"│ CPR Bot (BC)   │ {lvl['bc']:<{w2-2}} │",
        f"│ Range          │ {lvl['range']:<{w2-2}} │",
        f"├{'─'*w1}┼{'─'*w2}┤",
        f"│ BUY ABOVE      │ {lvl['b_above']:<{w2-2}} │",
        f"│ Buy Confirm    │ {lvl['b_conf']:<{w2-2}} │"
    ]
    for i in range(1, 11):
        lines.append(f"│ Target {i:<7} │ {lvl[f'bt{i}']:<{w2-2}} │")
    lines.append(f"├{'─'*w1}┼{'─'*w2}┤")
    lines.append(f"│ SELL BELOW     │ {lvl['s_below']:<{w2-2}} │")
    lines.append(f"│ Sell Confirm   │ {lvl['s_conf']:<{w2-2}} │")
    for i in range(1, 11):
        lines.append(f"│ Target {i:<7} │ {lvl[f'st{i}']:<{w2-2}} │")
    lines.append(f"└{'─'*w1}┴{'─'*w2}┘")
    return "\n".join(lines)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Universal Daily OP Calculator Bot\n\n"
        "Direct commands:\n"
        "• <code>GOLDMCX</code> (Gold 10g in INR)\n"
        "• <code>SILVERMCX</code> (Silver 1kg in INR)\n"
        "• <code>NIFTY</code>, <code>BANKNIFTY</code>, <code>SENSEX</code>\n"
        "• <code>RELIANCE</code>, <code>TCS</code>, <code>CRUDE</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def process_ticker(update: Update, query: str):
    await update.message.reply_text(f"Calculating OP levels for <b>{query.upper()}</b>...", parse_mode="HTML")
    df, matched_symbol = fetch_data(query)

    if df is None:
        await update.message.reply_text(f"Symbol <code>{query}</code> ka data fetch nahi ho paya.", parse_mode="HTML")
        return

    lvl = calculate_op_levels(df)
    ist = pytz.timezone('Asia/Kolkata')
    today_str = datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST')
    table = build_table(lvl)
    msg = f"<b>{matched_symbol} • OP LEVELS</b>\n<code>{today_str}</code>\n<pre>{table}</pre>"
    await update.message.reply_text(msg, parse_mode="HTML")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query.startswith("/"):
        await process_ticker(update, query)

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))
    print("Bot is polling live...")
    app.run_polling()

if __name__ == "__main__":
    main()
