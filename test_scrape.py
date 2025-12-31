import requests
from bs4 import BeautifulSoup
import pandas as pd

def get_buffett_holdings():
    url = "https://www.dataroma.com/m/holdings.php?m=BRK"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table', {'id': 'grid'})
        
        if not table:
            print("Table not found")
            return {}
            
        holdings = {}
        # Skip header row
        for row in table.findAll('tr')[1:]:
            cols = row.findAll('td')
            if len(cols) >= 2:
                # Column structure on Dataroma usually:
                # Stock (Symbol) | % Portfolio | Shares | Value | ...
                # Let's verify by printing a row
                # Symbol is usually in the first column <a> tag or text
                ticker_tag = cols[1].find('a')
                if ticker_tag:
                    ticker = ticker_tag.text.strip()
                    # Clean ticker (e.g. BRK.B -> BRK-B)
                    ticker = ticker.replace('.', '-')
                    
                    # Shares column (usually index 3, but need to check)
                    # Let's just print to verify first
                    # print(f"Row: {[c.text.strip() for c in cols]}")
                    
                    # Based on typical Dataroma structure:
                    # 0: Stock Name (with link) - wait, col 0 is name, col 1 is symbol usually?
                    # Let's inspect Dataroma structure via print
                    pass
        
        # Let's just return the raw rows for inspection first
        data = []
        for row in table.findAll('tr')[1:]:
            cols = [c.text.strip() for c in row.findAll('td')]
            if cols:
                data.append(cols)
        return data

    except Exception as e:
        print(f"Error: {e}")
        return {}

if __name__ == "__main__":
    data = get_buffett_holdings()
    if data:
        print(f"First 3 rows: {data[:3]}")
    else:
        print("No data found")
