# invest-lab-data
Weekly market-data snapshot for Invest_Lab (research only, no trading, no personal data).
- `data/weekly_prices.json` – closes for S&P 500, TA-125, five Israeli banks (+P/B) and the 20-stock consensus list.
- `data/risk_inputs.json` – FRED series (HY/IG spreads, NFCI, Sahm, claims, 10Y-3M, 10Y) and derived inputs (A2, A3, A4, A10).
Runs every Friday night on GitHub Actions (backup run Saturday morning). Manual run: Actions → weekly-data → Run workflow.
