import os
from flask import Flask, render_template, jsonify
from breakout_scanner.config import load_config
from breakout_scanner.scanner import MultiSymbolScanner
from breakout_scanner.providers.yfinance_provider import YFinanceProvider
from breakout_scanner.prediction_chat import explain

app = Flask(__name__)

# Initialize components
cfg = load_config('config/default.yaml')
provider = YFinanceProvider()
scanner = MultiSymbolScanner(provider, cfg)

# We'll scan a set of symbols
SYMBOLS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'TSLA', 'GOOGL', 'BTCUSD', 'ETHUSD', 'AMD']

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scan')
def api_scan():
    # Perform the scan
    setups = scanner.scan(SYMBOLS)
    
    # Format for JSON response
    results = []
    for s in setups:
        results.append({
            'symbol': s.symbol,
            'state': s.state.value,
            'trend': s.trend,
            'score': s.breakout_score,
            'explanation': explain(s),
            'evidence': s.evidence
        })
    
    return jsonify(results)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
