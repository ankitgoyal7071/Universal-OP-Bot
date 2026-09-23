import time
import requests

# अपने टेलीग्राम बोट का टोकन और चैनल/यूजर्स की चैट आईडी यहाँ डालें
BOT_TOKEN = "YOUR_BOT_TOKEN"
USER_CHAT_IDS = ["CHAT_ID_1", "CHAT_ID_2"] 

def broadcast_alert(message):
    """बोट पर जुड़े सभी यूजर्स या चैनल को एक साथ अलर्ट भेजने का फंक्शन"""
    for chat_id in USER_CHAT_IDS:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Error sending to {chat_id}: {e}")

def check_market_alerts(current_price, near_level, buy_above_level, t1_target, t2_target):
    """लेवल्स के हिसाब से स्टेप-बाय-स्टेप अलर्ट चेक करने का फंक्शन"""
    
    # 1. Ready for Entry (जब प्राइस बाय लेवल के करीब आने लगे)
    if near_level <= current_price < buy_above_level:
        msg = (
            "⚠️ *READY FOR ENTRY* ⚠️\n"
            "-----------------------------------\n"
            f"• Current Price: {current_price}\n"
            f"• Approaching Buy Level: {buy_above_level}"
        )
        broadcast_alert(msg)
    
    # 2. Entry Confirmed (जब बाय अबव लेवल क्रॉस हो जाए)
    elif current_price >= buy_above_level:
        msg = (
            "🚀 *ENTRY CONFIRMED (BUY ABOVE)* 🚀\n"
            "-----------------------------------\n"
            f"• Current Price: {current_price}\n"
            f"• Triggered Level: {buy_above_level}\n"
            f"• Target 1 (T1): {t1_target}\n"
            f"• Target 2 (T2): {t2_target}"
        )
        broadcast_alert(msg)
    
    # 3. Target 1 (T1) Hit
    if current_price >= t1_target:
        msg = (
            "🎯 *TARGET 1 (T1) HIT!* 🎯\n"
            "-----------------------------------\n"
            f"• Achieved Price: {current_price}\n"
            "• Book partial profits or trail SL!"
        )
        broadcast_alert(msg)
    
    # 4. Target 2 (T2) Hit
    if current_price >= t2_target:
        msg = (
            "🔥 *TARGET 2 (T2) HIT!* 🔥\n"
            "-----------------------------------\n"
            f"• Achieved Price: {current_price}\n"
            "• Massive movement captured successfully!"
        )
        broadcast_alert(msg)

if __name__ == "__main__":
    print("Bot is active and running with multi-user alert broadcast...")
    
    # यह while True लूप बोट को हमेशा ऑनलाइन रखेगा और 'Application exited early' की दिक्कत नहीं आएगी
    while True:
        try:
            # यहाँ अपना लाइव प्राइस फेच करने वाला लॉजिक रख सकते हैं
            pass
        except Exception as e:
            print(f"Loop Error: {e}")
        
        # हर 30 सेकंड में लूप रन होगा ताकि सर्वर पर लोड न आए
        time.sleep(30)
