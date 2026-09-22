import os
import threading
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import TelegramError

# आपका नया फ्रेश टोकन
TELEGRAM_BOT_TOKEN = "8983062859:AAGVLsG6R1wuZ2qLJu-Q5f150Ul8HRqAgMc"
ADMIN_ID = 1612210913

CHANNEL_USERNAME = "@LIVEOPTIONDATA"
CHANNEL_LINK = "https://t.me/LIVEOPTIONDATA"

# दूसरा बटन ठीक 30 सेकंड बाद स्क्रीन पर आएगा
MANDATORY_VISIT_SECONDS = 30

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
    "MCX": "MCX.NS",
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

async def is_user_subscribed(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except TelegramError as e:
        logging.error(f"Force Sub Error: {e}")
        return False

def get_force_sub_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel (@LIVEOPTIONDATA)", url=CHANNEL_LINK)],
        [InlineKeyboardButton("🔄 Verify Joined", callback_data="check_joined")]
    ])

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
    sym_u = symbol.upper().strip()
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
    out.append("<code>------------------------------</code>")
    out.append(f"  TC (Top Pivot)    : <b>{lvl['tc']}</b>")
    out.append(f"  P  (Central Pivot): <b>{lvl['pivot']}</b>")
    out.append(f"  BC (Bottom Pivot) : <b>{lvl['bc']}</b>")
    out.append(f"  Expected Range    : <b>{lvl['range']}</b>\n")

    out.append("🟢 <b>BUYING ZONE (BULLISH)</b>")
    out.append("<code>------------------------------</code>")
    out.append(f"  Buy Above         : <b>{lvl['b_above']}</b>")
    out.append(f"  Buy Confirm       : <b>{lvl['b_conf']}</b>")
    for i in range(1, 11):
        out.append(f"  Target {i:<2}         : <b>{lvl[f'bt{i}']}</b>")
    out.append("")

    out.append("🟠 <b>NO TRADE ZONE (RANGEBOUND)</b>")
    out.append("<code>------------------------------</code>")
    out.append(f"  Zone Range        : <b>{lvl['s_conf']}  ⇄  {lvl['b_conf']}</b>")
    out.append("  <i>(Strictly avoid new intraday positions here)</i>\n")

    out.append("🔴 <b>SELLING ZONE (BEARISH)</b>")
    out.append("<code>------------------------------</code>")
    out.append(f"  Sell Below        : <b>{lvl['s_below']}</b>")
    out.append(f"  Sell Confirm      : <b>{lvl['s_conf']}</b>")
    for i in range(1, 11):
        out.append(f"  Target {i:<2}         : <b>{lvl[f'st{i}']}</b>")

    return "\n".join(out)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    subscribed = await is_user_subscribed(context.bot, user_id)
    
    if not subscribed:
        msg = (
            "⚠️ <b>चैनल जॉइन करना अनिवार्य है!</b>\n\n"
            "बॉट का उपयोग करने के लिए नीचे दिए गए बटन से हमारे चैनल को जॉइन करें:"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_force_sub_markup())
        return

    msg = (
        "👋 <b>Universal OP Bot में आपका स्वागत है!</b>\n\n"
        "लेवल्स देखने के लिए सिम्बल का नाम भेजें (उदा. <code>NIFTY</code>, <code>BANKNIFTY</code>, <code>GOLDMCX</code>)."
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK)]])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=markup)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data == "check_joined":
        await query.answer()
        subscribed = await is_user_subscribed(context.bot, user_id)
        if subscribed:
            await query.message.delete()
            await query.message.reply_text("✅ <b>वेरिफिकेशन सफल!</b> अब लेवल्स के लिए सिम्बल भेजें (उदा. NIFTY, BANKNIFTY).", parse_mode="HTML")
        else:
            await query.message.reply_text("❌ आपने अभी तक चैनल जॉइन नहीं किया है। कृपया पहले जॉइन करें:", reply_markup=get_force_sub_markup())

    elif data.startswith("lvl_"):
        symbol = data.replace("lvl_", "").strip()

        subscribed = await is_user_subscribed(context.bot, user_id)
        if not subscribed:
            await query.answer("⚠️ कृपया पहले चैनल जॉइन करें!", show_alert=True)
            await query.message.reply_text("🔒 बॉट का उपयोग जारी रखने के लिए चैनल जॉइन करें:", reply_markup=get_force_sub_markup())
            return

        await query.answer()
        await query.message.delete()
        
        status_msg = await context.bot.send_message(
            chat_id=user_id, 
            text=f"⏳ <b>{symbol}</b> के OP लेवल्स निकाले जा रहे हैं...", 
            parse_mode="HTML"
        )
        
        try:
            df, matched_symbol = fetch_data(symbol)
            if df is None:
                await status_msg.edit_text(
                    f"❌ <b>'{symbol}'</b> का डेटा नहीं मिला।\n\n"
                    "कृपया सही सिम्बल लिखें (उदा. <code>NIFTY</code>, <code>BANKNIFTY</code>, <code>GOLDMCX</code>, <code>RELIANCE</code>).",
                    parse_mode="HTML"
                )
                return

            lvl = calculate_op_levels(df)
            ist = pytz.timezone('Asia/Kolkata')
            today_str = datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST')
            formatted_msg = format_color_display(matched_symbol, today_str, lvl)
            
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK)]])
            await status_msg.delete()
            await context.bot.send_message(chat_id=user_id, text=formatted_msg, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            logging.error(f"Error delivering level: {e}")
            await status_msg.edit_text("❌ लेवल्स निकालने में समस्या आई। कृपया पुनः प्रयास करें।")

async def delayed_unlock(bot, chat_id, message_id, query_symbol):
    """30 सेकंड बाद 'Get Levels' का बटन अनलॉक करता है"""
    await asyncio.sleep(MANDATORY_VISIT_SECONDS)
    
    unlocked_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Channel (@LIVEOPTIONDATA)", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ 2. Get Levels (अनलॉक हो गया)", callback_data=f"lvl_{query_symbol}")]
    ])
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"🎉 <b>{query_symbol} के लेवल्स अनलॉक हो चुके हैं!</b>\n\n"
                "30 सेकंड का समय पूरा हो गया है।\n"
                "लेवल्स देखने के लिए नीचे दिए गए बटन पर टैप करें:"
            ),
            parse_mode="HTML",
            reply_markup=unlocked_markup
        )
    except Exception as e:
        logging.error(f"Error updating message after delay: {e}")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.message.text.strip().upper()
    if query.startswith("/"):
        return

    subscribed = await is_user_subscribed(context.bot, user_id)
    if not subscribed:
        await update.message.reply_text("🔒 लेवल्स देखने के लिए पहले हमारे चैनल <b>@LIVEOPTIONDATA</b> को जॉइन करें।", parse_mode="HTML", reply_markup=get_force_sub_markup())
        return

    # शुरुआत में केवल चैनल देखने का 1 बटन होगा (दूसरा बटन स्क्रीन पर नहीं होगा)
    initial_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("👉 1. Visit Channel & View Ad (चैनल खोलें)", url=CHANNEL_LINK)]
    ])

    sent_msg = await update.message.reply_text(
        f"📊 <b>{query} के लेवल्स तैयार हैं!</b>\n\n"
        f"1. ऊपर दिए बटन से चैनल पर जाएँ और विज्ञापन/पोस्ट देखें।\n"
        f"⏳ दूसरा बटन <b>(Get Levels)</b> ठीक <b>{MANDATORY_VISIT_SECONDS} सेकंड</b> बाद यहीं अपने आप प्रकट होगा।",
        parse_mode="HTML",
        reply_markup=initial_markup
    )

    asyncio.create_task(delayed_unlock(context.bot, sent_msg.chat_id, sent_msg.message_id, query))

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))
    
    print("Bot is polling live purely with @LIVEOPTIONDATA channel flow...")
    app.run_polling()

if __name__ == "__main__":
    main()
