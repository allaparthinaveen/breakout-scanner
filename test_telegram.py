from breakout_scanner.models import Setup, State
from live_scanner import LiveScanner

def test_alert():
    print("Initializing LiveScanner for Test Alert...")
    scanner = LiveScanner()
    
    # Create a realistic "fake" setup for SOL-USD to test the notification formatting
    setup = Setup(symbol="SOL-USD")
    setup.state = State.BUY
    setup.direction = 1
    setup.entry = 155.40
    setup.breakout_level = 153.00
    setup.stop = 148.50
    setup.target = 169.20
    setup.evidence = [
        "tight base 8.4%",
        "breakout vol 2.1x",
        "strong close",
        "momentum ignition"
    ]
    
    print("Sending test payload to Telegram...")
    scanner.send_telegram_alert(setup)
    print("Test alert dispatched! Check your Telegram.")

if __name__ == "__main__":
    test_alert()
