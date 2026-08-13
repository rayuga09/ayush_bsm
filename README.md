# SPX BSM Options Analytics Dashboard

A production-quality **SPX options analytics terminal** built on the
**Black-Scholes-Merton (BSM)** model: full theoretical option chain,
first/second/third-order Greeks, interactive charts, Greek heatmaps and
self-explanatory exports — with a heavily tested, numerically stable
pricing engine.

> **Risk disclaimer:** All outputs are **theoretical model values**. They
> are not guarantees of market prices, execution prices, profitability or
> trading outcomes. Real markets exhibit volatility smile/skew, stochastic
> rates, discrete dividends, jumps, liquidity effects and bid/ask spreads
> that BSM does not capture. This tool provides analytics, not trading
> advice.

---

## Features

- **Manual, CSV/Excel and (pluggable) live data sources.** The live
  provider is intentionally *not configured* — no fabricated data, ever.
- **Automatic ATM detection** (nearest grid strike to spot) or explicit
  ATM override; configurable strike interval (default **25 points**) and
  strikes per side (default **28 + ATM + 28 = 57 strikes**).
- **Full Greek set:** Delta, Vega, Theta, Rho (first order); Gamma, Vanna,
  Volga/Vomma (second order); Charm, Speed, Zomma, Color, Ultima (third
  order and higher) — every one validated against finite differences.
- **Professional option chain** (CALL | STRIKE | PUT) with ATM
  highlighting, ITM shading, toggleable Greek groups, strike filtering,
  adjustable decimals, CSV and Excel export.
- **Interactive Plotly charts** for premiums and every Greek, plus a
  normalized **Strike × Greek heatmap**.
- **Model transparency:** formulas, assumptions, day-count and display
  conventions are shown inside the app.

## Mathematical Model

SPX options are **European-style and cash-settled**, so BSM (no
early-exercise logic) is the appropriate framework. With spot `S`, strike
`K`, time to expiry `T` (years), risk-free rate `r`, continuous dividend
yield `q` and volatility `σ`:

```text
d1 = [ln(S/K) + (r − q + σ²/2)T] / (σ√T)
d2 = d1 − σ√T

Call = S e^(−qT) N(d1) − K e^(−rT) N(d2)
Put  = K e^(−rT) N(−d2) − S e^(−qT) N(−d1)
```

Put-call parity `C − P = S e^(−qT) − K e^(−rT)` is verified on every
calculation and its maximum residual is displayed under the chain.

### Greek definitions (raw units)

| Greek | Definition | Formula (both sides unless noted) |
|---|---|---|
| Delta | ∂V/∂S | call: `e^(−qT) N(d1)` · put: `e^(−qT)(N(d1)−1)` |
| Gamma | ∂²V/∂S² | `e^(−qT) φ(d1) / (S σ√T)` |
| Vega | ∂V/∂σ | `S e^(−qT) φ(d1) √T` |
| Theta | ∂V/∂t | decay term ± dividend/rate carry (see module docstring) |
| Rho | ∂V/∂r | call: `K T e^(−rT) N(d2)` · put: `−K T e^(−rT) N(−d2)` |
| Vanna | ∂²V/∂S∂σ | `−e^(−qT) φ(d1) d2 / σ` |
| Volga | ∂²V/∂σ² | `Vega · d1 d2 / σ` |
| Charm | ∂Δ/∂t | `±q e^(−qT) N(±d1) − e^(−qT) φ(d1)·[2(r−q)T − d2σ√T]/(2Tσ√T)` |
| Speed | ∂³V/∂S³ | `−(Γ/S)(d1/(σ√T) + 1)` |
| Zomma | ∂Γ/∂σ | `Γ (d1 d2 − 1)/σ` |
| Color | ∂Γ/∂t | see `pricing/higher_order_greeks.py` |
| Ultima | ∂³V/∂σ³ | `−(Vega/σ²)[d1 d2(1 − d1 d2) + d1² + d2²]` |

### Greek display conventions (single source of truth: `pricing/conventions.py`)

| Displayed value | Convention |
|---|---|
| Theta, Charm, Color | per **calendar day** (annual / 365) |
| Vega, Vanna, Zomma, Rho | per **+1 percentage point** (raw × 0.01) |
| Volga | per (1 vol pct pt)² (raw × 10⁻⁴) |
| Ultima | per (1 vol pct pt)³ (raw × 10⁻⁶) |
| Delta, Gamma, Speed | per 1 index point (unscaled) |

Time-derivative Greeks (Theta, Charm, Color) use the **calendar-time**
convention ∂/∂t = −∂/∂T, verified sign-by-sign with finite differences.
Note that many references print Color as ∂Γ/∂T; ours is ∂Γ/∂t (hence the
opposite sign), consistent with Theta and Charm.

### Time to expiry

```text
T = remaining_seconds / (days_per_year × 86400)
```

with **ACT/365** by default (ACT/360 selectable). Full timestamps are
used, so same-day expiries with hours remaining get a small positive `T`;
`T ≤ 0` is treated as expired: prices become intrinsic values and Greeks
display **N/A** (they are mathematically undefined at expiry — the app
never shows fake finite values). Zero volatility likewise prices as
discounted forward intrinsic with N/A Greeks.

## Input Formats

Percentages are entered as percentages (`18.50` means σ = 0.185) and
converted internally exactly once, at the input boundary.

### CSV / Excel format

Both CSV (`.csv`) and Excel (`.xlsx`, `.xls`, `.xlsm`) uploads are
supported and behave identically. For Excel workbooks the first sheet is
used by default; if the workbook has several sheets, the app shows a
sheet selector. Fully empty rows/columns are ignored.

Required fields (flexible column names, see below): `spot`, `volatility`,
`risk_free_rate` and `expiry` (or `dte` in calendar days). Optional:
`dividend_yield` — if missing, **q = 0 is assumed and clearly flagged**.

Recognized aliases include:

| Field | Aliases |
|---|---|
| spot | `spot`, `spot_price`, `underlying_price`, `SPX`, `SPX_price`, `price`, ... |
| volatility | `volatility`, `vol`, `iv`, `implied_volatility`, `sigma`, ... |
| risk_free_rate | `rate`, `r`, `rf`, `risk_free`, `interest_rate`, ... |
| dividend_yield | `q`, `div_yield`, `dividend`, `yield`, ... |
| expiry | `expiry`, `expiration`, `maturity`, `exp_date`, ... or `dte` |

Ambiguous or missing columns trigger a **column-mapping UI** — the app
never guesses silently. Values like `18.5` for volatility are interpreted
as 18.5% (the interpretation is always reported in the sidebar).
See `sample_data/sample.csv`, `sample_data/sample_aliased_columns.csv`
and `sample_data/BSM inputs.xlsx`. A file can also be run from the CLI:
`python scripts/run_excel_file.py "sample_data/BSM inputs.xlsx" [row]`.

## Installation

```bash
pip install -r requirements.txt
```

Requires Python 3.10+ (developed on 3.13).

## Running the Dashboard

```bash
streamlit run app.py
```

## Testing

```bash
python -m pytest tests -q
```

710 tests cover: reference values (verified against independent
implementations), put-call parity across grids, deep ITM/OTM limits,
expiry and zero-volatility behavior, numerical stability under extreme
inputs, **finite-difference validation of every Greek** (the analytic
formula must match a numerical derivative of the price function — this
catches sign, scaling and convention errors), CSV alias detection and
percentage normalization, validators, date conventions and implied-vol
round-trips.

## Project Structure

```text
Dashboard/
├── app.py                     # Streamlit UI (orchestration only)
├── config/settings.py         # centralized constants and defaults
├── pricing/
│   ├── bsm.py                 # BSM core: d1/d2, prices, parity, regimes
│   ├── greeks.py              # first-order Greeks (+ Gamma)
│   ├── higher_order_greeks.py # Vanna, Volga, Charm, Speed, Zomma, Color, Ultima
│   ├── conventions.py         # single source of truth for display units
│   ├── registry.py            # Greek metadata registry (drives UI/export/docs)
│   ├── implied_vol.py         # Brent-based IV inversion (separate from pricing)
│   └── volatility.py          # VolatilityProvider abstraction (smile-ready)
├── analytics/chain.py         # strike grid, ATM detection, chain assembly
├── data/
│   ├── base.py                # DataProvider ABC + MarketInputs
│   ├── manual.py              # form-entered inputs
│   ├── csv_provider.py        # CSV/Excel parsing, alias detection, mapping
│   └── live_provider.py       # deliberately unconfigured (no fake data)
├── validation/validators.py   # human-readable errors and warnings
├── visualization/
│   ├── option_chain.py        # display scaling, CALL|STRIKE|PUT styling
│   └── charts.py              # Plotly charts + heatmap
├── utils/
│   ├── dates.py               # day-count conventions, T computation
│   ├── formatting.py          # display-only rounding
│   └── export.py              # self-explanatory CSV/Excel export
├── tests/                     # 710 tests incl. finite-difference validation
└── sample_data/               # example CSVs
```

## Data Providers

The pricing engine only consumes a `MarketInputs` object from a
`DataProvider`:

```text
DataProvider
    ├── ManualDataProvider      (form input)
    ├── CSVDataProvider         (upload + column mapping)
    └── LiveMarketDataProvider  (NOT configured; raises, never fabricates)
```

To integrate a real feed, implement `get_market_inputs()` in
`data/live_provider.py`, read credentials from environment variables or
Streamlit secrets (see `.env.example` — never hardcode keys), and return
real data or raise a clear error. The BSM mathematics needs no changes.
Volatility is similarly abstracted (`pricing/volatility.py`), so a smile
σ(K) or surface σ(K,T) can replace the constant vol without touching the
engine. `pricing/implied_vol.py` already provides robust price→IV
inversion for the future market-vs-model comparison.

## Limitations

- Single constant volatility per calculation (smile/surface is
  architecture-ready but not wired to a data source).
- No market option prices are ingested yet, so no market-vs-model
  comparison is displayed.
- Live data is not configured; the app says so instead of pretending.
- Continuous dividend yield only (no discrete dividend schedule).

## Future Roadmap

Live spot/chain/IV feeds → market-vs-model mispricing view → volatility
smile/skew and term structure → Greek surfaces, P&L simulator, payoff
diagrams → multi-leg strategies (condors, butterflies, spreads) →
scenario analysis (spot/vol/time/rate shocks) → Monte Carlo and
stochastic/local-volatility models. The provider abstractions, Greek
registry and convention system were designed so these arrive without
rewriting the engine.
