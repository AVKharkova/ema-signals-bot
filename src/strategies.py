import pandas as pd

def calc_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def calc_rsi(df, period=14):
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_gain = up.rolling(window=period, min_periods=period).mean()
    avg_loss = down.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def check_signal_ema7_30(df, limit):
    if len(df) < limit:
        return None
    df['ema7'] = calc_ema(df, 7)
    df['ema30'] = calc_ema(df, 30)
    if (
        df['ema7'].iloc[-2] < df['ema30'].iloc[-2] and
        df['ema7'].iloc[-1] > df['ema30'].iloc[-1] and
        df['close'].iloc[-1] > df['ema7'].iloc[-1] and
        df['close'].iloc[-1] > df['ema30'].iloc[-1]
    ):
        return "LONG"
    elif (
        df['ema7'].iloc[-2] > df['ema30'].iloc[-2] and
        df['ema7'].iloc[-1] < df['ema30'].iloc[-1] and
        df['close'].iloc[-1] < df['ema7'].iloc[-1] and
        df['close'].iloc[-1] < df['ema30'].iloc[-1]
    ):
        return "SHORT"
    return None

def check_signal_ema9_20_rsi(df, limit):
    if len(df) < limit:
        return None
    df['ema9'] = calc_ema(df, 9)
    df['ema20'] = calc_ema(df, 20)
    df['rsi'] = calc_rsi(df, 14)
    if pd.isna(df['rsi'].iloc[-1]) or pd.isna(df['rsi'].iloc[-2]):
        return None
    if (
        df['ema9'].iloc[-2] < df['ema20'].iloc[-2] and
        df['ema9'].iloc[-1] > df['ema20'].iloc[-1] and
        df['rsi'].iloc[-2] > 55 and
        df['rsi'].iloc[-1] <= 55
    ):
        return "LONG (RSI)"
    elif (
        df['ema9'].iloc[-2] > df['ema20'].iloc[-2] and
        df['ema9'].iloc[-1] < df['ema20'].iloc[-1] and
        df['rsi'].iloc[-2] < 45 and
        df['rsi'].iloc[-1] >= 45
    ):
        return "SHORT (RSI)"
    return None
