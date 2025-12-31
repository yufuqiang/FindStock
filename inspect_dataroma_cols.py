import requests
from bs4 import BeautifulSoup

def inspect_dataroma():
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
            return

        # Check headers
        thead = table.find('thead')
        if thead:
            headers = [th.text.strip() for th in thead.findAll('th')]
            print(f"Headers: {headers}")
        
        # Check first few rows
        rows = table.findAll('tr')
        print(f"Total rows: {len(rows)}")
        
        for i, row in enumerate(rows[:5]):
            cols = [c.text.strip() for c in row.findAll('td')]
            if cols:
                print(f"Row {i}: {cols}")
                # Print index and value for clarity
                for idx, val in enumerate(cols):
                    print(f"  [{idx}] {val}")
                    
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_dataroma()
