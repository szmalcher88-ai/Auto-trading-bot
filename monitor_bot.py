"""
Monitor for trading bot — reads trade_log.csv and displays stats.
Run in separate terminal: python monitor_bot.py

Refreshes every 30 seconds. Ctrl+C to stop.
"""

import csv
import os
import time


TRADE_LOG = 'trade_log.csv'


def load_trades():
    """Load all trades from CSV."""
    if not os.path.exists(TRADE_LOG):
        return []
    with open(TRADE_LOG, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)


def display(trades):
    """Display stats from trade log."""
    os.system('cls' if os.name == 'nt' else 'clear')

    print("=" * 60)
    print("  Trading Bot Monitor")
    print("=" * 60)
    print()

    if not trades:
        print("No trades yet.")
        return

    # Separate closes (have PnL) from opens
    closes = [t for t in trades if t.get('pnl_pct') and t['pnl_pct'] != '']

    # Last 10 trades
    print("Last 10 trades:")
    print(f"{'Time':>20} | {'Action':>12} | {'Price':>10} | {'PnL %':>8} | {'Reason':>12}")
    print("-" * 70)
    for t in trades[-10:]:
        ts = t.get('timestamp', '')[:19]
        action = t.get('action', '')
        price = t.get('fill_price', '') or t.get('exit_price', '') or t.get('entry_price', '')
        pnl = t.get('pnl_pct', '')
        reason = t.get('exit_reason', '')
        pnl_str = f"{float(pnl):+.2f}%" if pnl else "-"
        print(f"{ts:>20} | {action:>12} | {price:>10} | {pnl_str:>8} | {reason:>12}")

    print()

    if closes:
        # Win rate
        wins = sum(1 for t in closes if float(t['pnl_pct']) > 0)
        losses = sum(1 for t in closes if float(t['pnl_pct']) <= 0)
        total = wins + losses
        win_rate = (wins / total * 100) if total > 0 else 0

        # Total PnL
        total_pnl_pct = sum(float(t['pnl_pct']) for t in closes)
        total_pnl_usd = sum(float(t.get('pnl_usd', 0)) for t in closes if t.get('pnl_usd'))

        # Consecutive losses (current streak)
        consec = 0
        for t in reversed(closes):
            if float(t['pnl_pct']) < 0:
                consec += 1
            else:
                break

        # Last balance
        last_balance = closes[-1].get('balance_after', 'N/A')

        print(f"Total closed trades: {total}")
        print(f"Win rate: {win_rate:.1f}% ({wins}W / {losses}L)")
        print(f"Total PnL: {total_pnl_pct:+.2f}% ({total_pnl_usd:+.2f} USD)")
        print(f"Consecutive losses: {consec}")
        print(f"Last balance: {last_balance} USDT")
    else:
        print("No closed trades yet.")


def main():
    print("Monitor starting... (Ctrl+C to stop)")
    while True:
        try:
            trades = load_trades()
            display(trades)
            print()
            print("Refreshing in 30s...")
            time.sleep(30)
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
            break


if __name__ == '__main__':
    main()
