import yfinance as yf
try:
    ticker = yf.Ticker("AAPL")
    info = ticker.info
    print(f"Name: {info.get('shortName')}")
    print("Success")
except Exception as e:
    print(f"Error: {e}")
