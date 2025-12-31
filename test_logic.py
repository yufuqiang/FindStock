import yfinance as yf
import pandas as pd
import concurrent.futures

def process_ticker(ticker):
    print(f"Analyzing {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        if not info:
            print(f"No info for {ticker}")
            return None
            
        print(f"Got info for {ticker}: ROE={info.get('returnOnEquity')}, DE={info.get('debtToEquity')}, GM={info.get('grossMargins')}, PE={info.get('trailingPE')}")
        
        # Buffett criteria
        roe = info.get('returnOnEquity', 0)
        if roe is None or roe < 0.15:
            return None
            
        de_ratio = info.get('debtToEquity', 1000)
        if de_ratio is None or de_ratio > 100:
            return None
            
        gross_margins = info.get('grossMargins', 0)
        if gross_margins is None or gross_margins < 0.4:
            return None
            
        pe = info.get('trailingPE', 0)
        if pe is None or pe <= 0 or pe > 30:
            return None
        
        return ticker
    except Exception as e:
        print(f"Error analyzing {ticker}: {e}")
        return None

def test_logic():
    # Test with a few known stocks
    # AAPL (usually high ROE, decent margins)
    # KO (Coca Cola - Buffett favorite)
    # TSLA (Growth, might fail PE or DE)
    test_tickers = ['AAPL', 'KO', 'MSFT', 'NVDA', 'BRK-B']
    
    print("Starting test...")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_ticker, ticker): ticker for ticker in test_tickers}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                
    print(f"Selected stocks from test batch: {results}")

if __name__ == "__main__":
    test_logic()
