"""Run the full analytics pipeline on an Excel/CSV input file from the CLI.

Usage:
    python scripts/run_excel_file.py "sample_data/BSM inputs.xlsx" [row]

Mirrors exactly what the dashboard does on upload: parse -> detect columns
-> build market inputs -> validate -> compute chain -> print a summary.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.chain import ChainConfig, build_option_chain
from data.csv_provider import CSVDataProvider, detect_columns, parse_tabular
from pricing.volatility import ConstantVolatility
from utils.dates import time_to_expiry
from validation.validators import validate_market_inputs


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_data/BSM inputs.xlsx")
    row = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    df, report = parse_tabular(path.read_bytes(), path.name)
    if df is None:
        for err in report.errors:
            print("ERROR:", err)
        raise SystemExit(1)
    report = detect_columns(df, report)
    sheet = f", sheet {report.sheet_used!r}" if report.sheet_used else ""
    print(f"Parsed {report.filename} ({report.file_format}{sheet}): "
          f"{report.n_rows} rows x {report.n_cols} cols")
    print("Detected mapping:", report.detected)
    if not report.ok:
        print("Missing:", report.missing, "| Ambiguous:", report.ambiguous)
        raise SystemExit(1)

    provider = CSVDataProvider(df, report.detected, row_index=row,
                               source_label=report.file_format)
    mi = provider.get_market_inputs()
    for note in provider.interpretations:
        print("  note:", note)

    T = time_to_expiry(mi.expiry)
    val = validate_market_inputs(mi.spot, mi.volatility, mi.risk_free_rate,
                                 mi.dividend_yield, T)
    for msg in val.errors:
        print("ERROR:", msg)
    for msg in val.warnings:
        print("WARNING:", msg)
    if not val.ok:
        raise SystemExit(1)

    chain, meta = build_option_chain(
        mi.spot, T, mi.risk_free_rate, mi.dividend_yield,
        ConstantVolatility(mi.volatility), ChainConfig(), mi.expiry)

    print()
    print(f"Source={mi.source}  Spot={meta.spot:,.2f}  "
          f"ATM={meta.atm_strike:,.0f}  DTE={meta.dte_days:.2f}d  "
          f"T={meta.T:.6f}y  strikes={meta.n_strikes}")
    print(f"sigma={mi.volatility:.4%}  r={mi.risk_free_rate:.4%}  "
          f"q={mi.dividend_yield:.4%}"
          + ("  (q assumed)" if mi.dividend_assumed else ""))
    atm = chain[chain["is_atm"]].iloc[0]
    print(f"ATM {atm['strike']:.0f}:  Call={atm['call_price']:.2f}  "
          f"Put={atm['put_price']:.2f}  dC={atm['call_delta']:.4f}  "
          f"dP={atm['put_delta']:.4f}  gamma={atm['gamma']:.6f}  "
          f"vega/1pct={atm['vega'] * 0.01:.3f}  "
          f"thetaC/day={atm['call_theta'] / 365:.3f}")
    print(f"max |parity error| = {chain['parity_error'].abs().max():.3e}")


if __name__ == "__main__":
    main()
