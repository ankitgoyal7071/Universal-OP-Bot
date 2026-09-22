import logging
import requests
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
    "BITCOIN": "BTC-USD",
    "ETH": "ETH-USD",
    "ETHEREUM": "ETH-USD",
    "SOL": "SOL-USD",
    "DOGE": "DOGE-USD",
    "USDINR": "USDINR=X",
    "EURINR": "EURINR=X",
    "GBPINR": "GBPINR=X"
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

    # Buy Levels & 10 Targets
    b_above = round(doKdc + (range1 * 0.236))
    b_conf  = round(doKdc + (range1 * 0.382))
    bt1     = round(doKdc + (range1 * 0.500))
    bt2     = round(doKdc + (range1 * 0.618))
    bt3     = round(doKdc + (range1 * 0.786))
    bt4     = round(doKdc + (range1 * 1.000))
    bt5     = round(doKdc + (range1 * 1.272))
    bt6     = round(doKdc + (range1 * 1.618))
    bt7     = round(doKdc + (range1 * 2.000))
    bt8     = round(doKdc + (range1 * 2.272))
    bt9     = round(doKdc + (range1 * 2.618))
    bt10    = round(doKdc + (range1 * 3.000))

    # Sell Levels & 10 Targets
    s_below = round(doKdc - (range1 * 0.236))
    s_conf  = round(doKdc - (range1 * 0.382))
    st1     = round(doKdc - (range1 * 0.500))
    st2     = round(doKdc - (range1 * 0.618))
    st3     = round(doKdc - (range1 * 0.786))
    st4     = round(doKdc - (range1 * 1.000))
    st5     = round(doKdc - (range1 * 1.272))
    st6     = round(doKdc - (range1 * 1.618))
    st7     = round(doKdc - (range1 * 2.000))
    st8     = round(doKdc - (range1 * 2.272))
    st9     = round(doKdc - (range1 * 2.618))
    st10    = round(doKdc - (range1 * 3.000))

    return {
        "range": range1, "pivot": pivot, "tc": tc, "bc": bc,
        "b_above": b_above, "b_conf": b_conf,
        "bt1": bt1, "bt2": bt2, "bt3": bt3, "bt4": bt4, "bt5": bt5,
        "bt6": bt6, "bt7": bt7, "bt8": bt8, "bt9": bt9, "bt10": bt10,
        "s_below": s_below, "s_conf": s_conf,
        "st1": st1, "st2": st2, "st3": st3, "st4": st4, "st5": st5,
        "st6": st6, "st7": st7, "st8": st8, "st9": st9, "st10": st10
    }

def fetch_data(symbol):
    sym = ALIAS_MAP.get(symbol.upper(), symbol.upper())
    symbols_to_try = [sym]
    if not any(sym.endswith(ext) for ext in [".NS", ".BO", "=F", "=X", "-USD"]) and not sym.startswith("^"):
        symbols_to_try.append(f"{sym}.NS")
        symbols_to_try.append(f"{sym}.BO")
        symbols_to_try.append(f"{sym}-USD")

    for s in symbols_to_try:
        try:
            ticker = yf.Ticker(s)
            df = ticker.history(period="3mo", interval="1d")
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
    table_lines = [
        f"┌{'─'*w1}┬{'─'*w2}┐",
        f"│ {'LEVEL TYPE':<{w1-2}} │ {'PRICE':<{w2-2}} │",
        f"├{'─'*w1}┼{'─'*w2}┤",
        f"│ CPR Top (TC)   │ {lvl['tc']:<{w2-2}} │",
        f"│ Pivot (P)      │ {lvl['pivot']:<{w2-2}} │",
        f"│ CPR Bot (BC)   │ {lvl['bc']:<{w2-2}} │",
        f"│ Range          │ {lvl['range']:<{w2-2}} │",
        f"├{'─'*w1}┼{'─'*w2}┤",
        f"│ BUY ABOVE      │ {lvl['b_above']:<{w2-2}} │",
        f"│ Buy Confirm    │ {lvl['b_conf']:<{w2-2}} │",
        f"│ Target 1       │ {lvl['bt1']:<{w2-2}} │",
        f"│ Target 2       │ {lvl['bt2']:<{w2-2}} │",
        f"│ Target 3       │ {lvl['bt3']:<{w2-2}} │",
        f"│ Target 4       │ {lvl['bt4']:<{w2-2}} │",
        f"│ Target 5       │ {lvl['bt5']:<{w2-2}} │",
        f"│ Target 6       │ {lvl['bt6']:<{w2-2}} │",
        f"│ Target 7       │ {lvl['bt7']:<{w2-2}} │",
        f"│ Target 8       │ {lvl['bt8']:<{w2-2}} │",
        f"│ Target 9       │ {lvl['bt9']:<{w2-2}} │",
        f"│ Target 10      │ {lvl['bt10']:<{w2-2}} │",
        f"├{'─'*w1}┼{'─'*w2}┤",
        f"│ SELL BELOW     │ {lvl['s_below']:<{w2-2}} │",
        f"│ Sell Confirm   │ {lvl['s_conf']:<{w2-2}} │",
        f"│ Target 1       │ {lvl['st1']:<{w2-2}} │",
        f"│ Target 2       │ {lvl['st2']:<{w2-2}} │",
        f"│ Target 3       │ {lvl['st3']:<{w2-2}} │",
        f"│ Target 4       │ {lvl['st4']:<{w2-2}} │",
        f"│ Target 5       │ {lvl['st5']:<{w2-2}} │",
        f"│ Target 6       │ {lvl['st6']:<{w2-2}} │",
        f"│ Target 7       │ {lvl['st7']:<{w2-2}} │",
        f"│ Target 8       │ {lvl['st8']:<{w2-2}} │",
        f"│ Target 9       │ {lvl['st9']:<{w2-2}} │",
        f"│ Target 10      │ {lvl['st10']:<{w2-2}} │",
        f"└{'─'*w1}┴{'─'*w2}┘"
    ]
    return "\n".join(table_lines)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 <b>Universal Daily OP Calculator Bot</b>\n\n"
        "Aap kisi bhi stock, index, crypto ya commodity ka naam bhejein.\n\n"
        "<b>Examples:</b>\n"
        "• <code>RELIANCE</code> ya <code>TCS</code>\n"
        "• <code>NIFTY</code> ya <code>BANKNIFTY</code>\n"
        "• <code>GOLD</code> ya <code>CRUDE</code>\n"
        "• <code>BTC</code> ya <code>ETH</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def process_ticker(update: Update, query: str):
    await update.message.reply_text(f"⏳ Calculating OP levels for <b>{query.upper()}</b>...", parse_mode="HTML")
    df, matched_symbol = fetch_data(query)

    if df is None:
        await update.message.reply_text(
            f"❌ Symbol <code>{query}</code> ka data fetch nahi ho paya. Sahi ticker name check karein.",
            parse_mode="HTML"
        )
        return

    lvl = calculate_op_levels(df)
    ist = pytz.timezone('Asia/Kolkata')
    today_str = datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST')
    
    table = build_table(lvl)
    header = f"📊 <b>{matched_symbol} • OP LEVELS</b>\n📅 <code>{today_str}</code>\n"
    msg = f"{header}<pre>{table}</pre>"

    await update.message.reply_text(msg, parse_mode="HTML")

async def level_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Symbol saath me likhein: <code>/level RELIANCE</code>", parse_mode="HTML")
        return
    await process_ticker(update, context.args[0])

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if query.startswith("/"):
        return
    await process_ticker(update, query)

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("level", level_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))
    print("Bot is polling live...")
    app.run_polling()

if __name__ == "__main__":
    main()
  
