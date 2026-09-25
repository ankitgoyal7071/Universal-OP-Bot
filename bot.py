def fetch_data(symbol):
    try:
        sym_u = symbol.upper().strip()
        clean_sym = sym_u.replace("📊", "").replace("📈", "").replace("🪙", "").replace("💱", "").replace("⚡", "").replace("🥇", "").replace("🥈", "").replace("🛢️", "").replace("🔥", "").strip()
        
        is_mcx = "MCX" in clean_sym
        
        if "XAU" in clean_sym or "SPOT GOLD" in clean_sym:
            resolved = "XAUUSD=X"
        elif "XAG" in clean_sym or "SPOT SILVER" in clean_sym:
            resolved = "XAGUSD=X"
        elif "MCX GOLD" in clean_sym:
            resolved = "GC=F"
        elif "MCX SILVER" in clean_sym:
            resolved = "SI=F"
        elif "MCX CRUDE" in clean_sym:
            resolved = "CL=F"
        elif "MCX NATURAL" in clean_sym:
            resolved = "NG=F"
        elif "GLOBAL CRUDE" in clean_sym:
            resolved = "CL=F"
        elif "GLOBAL NATURAL" in clean_sym:
            resolved = "NG=F"
        elif "BITCOIN" in clean_sym or clean_sym == "BTC":
            resolved = "BTC-USD"
        elif "ETHEREUM" in clean_sym or clean_sym == "ETH":
            resolved = "ETH-USD"
        else:
            resolved = ALIAS_MAP.get(clean_sym, clean_sym)

        candidates = [resolved]
        if not any(resolved.endswith(x) for x in [".NS", ".BO", "=F", "=X", "-USD"]) and not resolved.startswith("^"):
            candidates.extend([f"{resolved}.NS", f"{resolved}.BO", f"{resolved}-USD", f"{resolved}=X"])

        for s in candidates:
            try:
                if is_mcx:
                    # कमोडिटी और USDINR का डेटा एक ही समय पर सिंक्रोनाइज्ड फेच करना
                    tickers_to_fetch = [s, "USDINR=X"]
                    data = yf.download(tickers_to_fetch, period="5d", interval="1d", progress=False, group_by='ticker')
                    
                    if not data.empty and s in data and "USDINR=X" in data:
                        df = data[s].dropna()
                        usdinr_df = data["USDINR=X"].dropna()
                        
                        if len(df) >= 12 and not usdinr_df.empty:
                            usdinr_rate = float(usdinr_df['Close'].iloc[-1])
                            
                            # यहाँ ग्लोबल प्राइस * लाइव एक्सचेंज रेट * यूनिट * 1.143 फैक्टर लागू हो रहा है
                            if "GOLD" in clean_sym:
                                multiplier = (1 / 31.1035) * 10 * usdinr_rate * 1.143
                            elif "SILVER" in clean_sym:
                                multiplier = (1 / 31.1035) * 1000 * usdinr_rate * 1.143
                            elif "CRUDE" in clean_sym:
                                multiplier = usdinr_rate * 1.143 * 1.35
                            elif "NATURAL" in clean_sym:
                                multiplier = usdinr_rate * 1.143 * 2.10
                            else:
                                multiplier = usdinr_rate * 1.143
                            
                            for col in ['Open', 'High', 'Low', 'Close']:
                                if col in df.columns:
                                    df[col] = df[col] * multiplier
                            return df, s

                df = yf.Ticker(s).history(period="3mo", interval="1d")
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna()
                if len(df) >= 12:
                    return df, s
            except Exception:
                continue
    except Exception as e:
        logging.error(f"Fetch Data Error: {e}")
    return None, None
