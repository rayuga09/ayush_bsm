"""SPX BSM Options Analytics dashboard (Streamlit entry point).

UI orchestration only -- all mathematics lives in ``pricing``/``analytics``,
data ingestion in ``data``, validation in ``validation`` and presentation
helpers in ``visualization``/``utils``.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd
import streamlit as st

from analytics.chain import ChainConfig, build_option_chain
from config import settings
from data.base import DataProviderError, MarketInputs
from data.csv_provider import (CSVDataProvider, EXCEL_EXTENSIONS,
                               detect_columns, parse_tabular)
from data.manual import ManualDataProvider
from pricing.conventions import DEFAULT_CONVENTIONS
from pricing.registry import GREEK_GROUPS, GREEK_REGISTRY, GREEKS_BY_KEY
from pricing.volatility import ConstantVolatility
from utils import export as export_utils
from utils.dates import DayCount, time_to_expiry
from utils.formatting import fmt_number, fmt_pct
from validation.validators import validate_chain_config, validate_market_inputs
from visualization import charts
from visualization.option_chain import (GROUP_GREEKS, build_display_frame,
                                        filter_chain, style_chain)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spx_bsm_dashboard")

st.set_page_config(page_title=settings.APP_TITLE, page_icon="chart_with_upwards_trend",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.6rem; padding-bottom: 2rem; }
      div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 8px; padding: 10px 14px;
      }
      div[data-testid="stMetricLabel"] { font-size: 0.78rem; opacity: 0.75; }
      .disclaimer {
        font-size: 0.8rem; opacity: 0.75; border-left: 3px solid #ffc400;
        padding: 6px 12px; margin: 6px 0 14px 0;
        background: rgba(255,196,0,0.06); border-radius: 4px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Cached computation
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=64)
def compute_chain(spot: float, sigma: float, r: float, q: float, T: float,
                  interval: float, each_side: int, atm_method: str,
                  explicit_atm: float | None, expiry_iso: str):
    cfg = ChainConfig(strike_interval=interval, strikes_each_side=each_side,
                      atm_method=atm_method, explicit_atm=explicit_atm)
    df, meta = build_option_chain(
        spot=spot, T=T, r=r, q=q,
        vol_provider=ConstantVolatility(sigma),
        cfg=cfg, expiry=datetime.fromisoformat(expiry_iso),
    )
    return df, meta


# ---------------------------------------------------------------------------
# Sidebar: data source + inputs
# ---------------------------------------------------------------------------
def sidebar_market_inputs() -> MarketInputs | None:
    """Render the Data Source section; return market inputs or None."""
    st.sidebar.header("Data Source")
    mode = st.sidebar.radio(
        "Source", ["Manual", "CSV / Excel", "Live API"],
        horizontal=True, label_visibility="collapsed")

    if mode == "Live API":
        st.sidebar.error(
            "Live data source: **Not configured**\n\n"
            "No live market-data provider has been set up. The architecture "
            "supports plugging one in (see README: Data Providers); no "
            "fabricated prices will ever be shown."
        )
        return None

    if mode == "CSV / Excel":
        return sidebar_file_inputs()
    return sidebar_manual_inputs()


def _expiry_widget(key_prefix: str) -> datetime:
    st.sidebar.subheader("Contract Inputs")
    col1, col2 = st.sidebar.columns(2)
    exp_date = col1.date_input("Expiry date", value=date.today() + timedelta(days=30),
                               key=f"{key_prefix}_exp_date")
    exp_time = col2.time_input("Expiry time", value=dtime(16, 0),
                               key=f"{key_prefix}_exp_time",
                               help="SPX weeklies settle 4:00 pm ET; AM-settled "
                                    "monthlies use the 9:30 am opening print. "
                                    "Times are interpreted in your local clock.")
    return datetime.combine(exp_date, exp_time)


def sidebar_manual_inputs() -> MarketInputs:
    st.sidebar.subheader("Market Inputs")
    spot = st.sidebar.number_input("Spot price (S)", min_value=0.01,
                                   value=settings.DEFAULT_SPOT, step=1.0,
                                   format="%.2f")
    vol_pct = st.sidebar.number_input(
        "Volatility \u03c3 (%)", min_value=0.0, max_value=500.0,
        value=settings.DEFAULT_VOLATILITY * 100, step=0.25, format="%.2f",
        help="Enter as a percentage: 18.50 means \u03c3 = 0.185.")
    rate_pct = st.sidebar.number_input(
        "Risk-free rate r (%)", min_value=-100.0, max_value=100.0,
        value=settings.DEFAULT_RISK_FREE_RATE * 100, step=0.05, format="%.2f")
    no_div = st.sidebar.checkbox(
        "No dividend input (assume q = 0%)", value=False,
        help="BSM for SPX should use a continuous dividend yield. Only check "
             "this if you deliberately want the q = 0 fallback.")
    div_pct = st.sidebar.number_input(
        "Dividend yield q (%)", min_value=-50.0, max_value=100.0,
        value=settings.DEFAULT_DIVIDEND_YIELD * 100, step=0.05, format="%.2f",
        disabled=no_div)

    expiry = _expiry_widget("manual")
    provider = ManualDataProvider(
        spot=spot, volatility=vol_pct / 100.0, risk_free_rate=rate_pct / 100.0,
        dividend_yield=None if no_div else div_pct / 100.0, expiry=expiry)
    return provider.get_market_inputs()


def sidebar_file_inputs() -> MarketInputs | None:
    uploaded = st.sidebar.file_uploader(
        "Upload CSV or Excel", type=["csv", "xlsx", "xls", "xlsm"],
        help="See README / sample_data for the expected fields.")
    if uploaded is None:
        st.sidebar.info("Upload a CSV or Excel file with spot, volatility, "
                        "risk-free rate, dividend yield and expiry (or dte). "
                        "Common aliases like `SPX_price` or `iv` are "
                        "auto-detected.")
        return None

    # For Excel workbooks with several sheets, let the user pick one.
    sheet_name = None
    if uploaded.name.lower().endswith(EXCEL_EXTENSIONS):
        _, probe = parse_tabular(uploaded.getvalue(), uploaded.name)
        if len(probe.sheets) > 1:
            sheet_name = st.sidebar.selectbox("Excel sheet", probe.sheets)

    df_csv, report = parse_tabular(uploaded.getvalue(), uploaded.name,
                                   sheet_name)
    if df_csv is None:
        for err in report.errors:
            st.sidebar.error(err)
        return None
    report = detect_columns(df_csv, report)

    with st.sidebar.expander("File preview & validation", expanded=True):
        sheet_info = (f", sheet '{report.sheet_used}'"
                      if report.sheet_used else "")
        st.caption(f"**{report.filename}** ({report.file_format}{sheet_info}) "
                   f"\u2014 {report.n_rows} rows, {report.n_cols} columns")
        st.dataframe(df_csv.head(5), height=150)
        if report.detected:
            st.caption("Detected: " + ", ".join(
                f"`{col}` \u2192 {fld}" for fld, col in report.detected.items()))
        if report.missing:
            st.warning("Missing fields: " + ", ".join(report.missing)
                       + ". Map them below or fix the file.")
        if report.ambiguous:
            st.warning("Ambiguous columns for: "
                       + ", ".join(report.ambiguous) + ". Choose below.")

    # Column mapping UI: prefilled with detections, manual override allowed.
    mapping: dict[str, str] = {}
    st.sidebar.caption("Column mapping")
    none_label = "\u2014 not present \u2014"
    for fld in ["spot", "volatility", "risk_free_rate", "dividend_yield",
                "expiry", "dte"]:
        options = [none_label] + list(df_csv.columns)
        default = report.detected.get(fld)
        index = options.index(default) if default in options else 0
        chosen = st.sidebar.selectbox(fld, options, index=index,
                                      key=f"map_{fld}")
        if chosen != none_label:
            mapping[fld] = chosen

    row_ix = 0
    if len(df_csv) > 1:
        row_ix = st.sidebar.number_input(
            "Row to price (file has multiple rows)", min_value=0,
            max_value=len(df_csv) - 1, value=0, step=1)

    required_ok = all(f in mapping for f in ["spot", "volatility",
                                             "risk_free_rate"])
    if not required_ok or ("expiry" not in mapping and "dte" not in mapping):
        st.sidebar.error("Please map spot, volatility, risk-free rate and "
                         "expiry (or dte) to proceed.")
        return None

    provider = CSVDataProvider(df_csv, mapping, row_index=int(row_ix),
                               source_label=report.file_format)
    try:
        mi = provider.get_market_inputs()
    except DataProviderError as exc:
        st.sidebar.error(str(exc))
        return None
    for note in provider.interpretations:
        st.sidebar.caption(f"\u2139\ufe0f {note}")
    return mi


def sidebar_chain_inputs() -> tuple[ChainConfig, DayCount]:
    st.sidebar.subheader("Strike Grid")
    interval = st.sidebar.number_input(
        "Strike interval", min_value=0.5, value=settings.DEFAULT_STRIKE_INTERVAL,
        step=5.0, format="%.1f")
    each_side = st.sidebar.number_input(
        "Strikes each side of ATM", min_value=0, max_value=500,
        value=settings.DEFAULT_STRIKES_EACH_SIDE, step=1,
        help=f"Total strikes = 2 x this + 1 (default "
             f"{2 * settings.DEFAULT_STRIKES_EACH_SIDE + 1}).")
    atm_method = st.sidebar.radio(
        "ATM method", ["Nearest grid strike to spot", "Explicit ATM strike"])
    explicit_atm = None
    if atm_method == "Explicit ATM strike":
        explicit_atm = st.sidebar.number_input("ATM strike", min_value=0.01,
                                               value=6125.0, step=25.0)
    day_count = st.sidebar.selectbox(
        "Day count", [DayCount.ACT_365, DayCount.ACT_360],
        format_func=lambda d: d.value,
        help="Convention for converting remaining time to T (years). "
             "T = remaining_seconds / (days_per_year x 86400).")
    cfg = ChainConfig(
        strike_interval=float(interval), strikes_each_side=int(each_side),
        atm_method="explicit" if explicit_atm is not None else "nearest",
        explicit_atm=explicit_atm)
    return cfg, day_count


# ---------------------------------------------------------------------------
# Result sections
# ---------------------------------------------------------------------------
def render_kpis(meta, mi: MarketInputs, df: pd.DataFrame) -> None:
    atm_row = df[df["is_atm"]]
    atm_call = float(atm_row["call_price"].iloc[0]) if len(atm_row) else float("nan")
    atm_put = float(atm_row["put_price"].iloc[0]) if len(atm_row) else float("nan")

    row1 = st.columns(4)
    row1[0].metric("SPX Spot", f"{meta.spot:,.2f}")
    row1[1].metric("ATM Strike", f"{meta.atm_strike:,.0f}",
                   delta=f"Spot \u2212 ATM: {meta.spot_minus_atm:+,.2f}",
                   delta_color="off")
    row1[2].metric("DTE", f"{meta.dte_days:,.2f} days",
                   delta=f"T = {meta.T:.6f} yrs", delta_color="off")
    row1[3].metric("Volatility \u03c3", fmt_pct(mi.volatility))

    row2 = st.columns(4)
    row2[0].metric("ATM BSM Call", fmt_number(atm_call, 2))
    row2[1].metric("ATM BSM Put", fmt_number(atm_put, 2))
    row2[2].metric("Risk-Free Rate", fmt_pct(mi.risk_free_rate))
    q_label = fmt_pct(mi.dividend_yield)
    if mi.dividend_assumed:
        q_label += " (assumed)"
    row2[3].metric("Dividend Yield", q_label)


def render_chain_tab(df: pd.DataFrame, meta, inputs: dict) -> None:
    c1, c2, c3, c4 = st.columns([2.2, 1.2, 1.6, 1.4])
    groups = c1.multiselect("Greek groups", GREEK_GROUPS,
                            default=["First Order", "Second Order"])
    decimals = c2.slider("Decimals", 0, 8, settings.DEFAULT_DECIMALS)
    lo, hi = float(df["strike"].min()), float(df["strike"].max())
    strike_range = c3.slider("Strike range", lo, hi, (lo, hi),
                             step=float(inputs["interval"]))
    quick = c4.selectbox("Filter", ["All strikes", "Near ATM (\u00b110 strikes)",
                                    "ITM calls", "ITM puts"])

    filtered = filter_chain(df, strike_range[0], strike_range[1], quick)
    display = build_display_frame(filtered, meta, DEFAULT_CONVENTIONS, groups)
    st.dataframe(style_chain(display, filtered, decimals), height=620)

    max_parity = float(df["parity_error"].abs().max())
    st.caption(
        f"Put-call parity check: max |C \u2212 P \u2212 (S e^(\u2212qT) "
        f"\u2212 K e^(\u2212rT))| = {max_parity:.3e} "
        f"{'\u2705' if max_parity < 1e-8 else '\u26a0\ufe0f investigate'} "
        f"\u00b7 ATM row highlighted in amber \u00b7 ITM cells shaded green "
        f"\u00b7 N/A = mathematically undefined (e.g. at expiry)")

    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button(
        "\u2b07 Download CSV",
        data=export_utils.to_csv_bytes(df, meta, inputs, DEFAULT_CONVENTIONS),
        file_name="spx_bsm_option_chain.csv", mime="text/csv")
    d2.download_button(
        "\u2b07 Download Excel",
        data=export_utils.to_excel_bytes(df, meta, inputs, DEFAULT_CONVENTIONS),
        file_name="spx_bsm_option_chain.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def render_charts_tab(df: pd.DataFrame, meta) -> None:
    st.plotly_chart(charts.premium_chart(df, meta), width="stretch")
    default_keys = ["delta", "gamma", "vega", "theta", "rho", "vanna",
                    "volga", "charm"]
    keys = st.multiselect(
        "Greek charts", [g.key for g in GREEK_REGISTRY], default=default_keys,
        format_func=lambda k: GREEKS_BY_KEY[k].label)
    cols = st.columns(2)
    for i, key in enumerate(keys):
        with cols[i % 2]:
            st.plotly_chart(charts.greek_chart(df, meta, key,
                                               DEFAULT_CONVENTIONS),
                            width="stretch")


def render_heatmap_tab(df: pd.DataFrame, meta) -> None:
    c1, c2 = st.columns([1, 3])
    side = c1.radio("Side", ["call", "put"], horizontal=True,
                    format_func=str.upper)
    keys = c2.multiselect(
        "Greeks", [g.key for g in GREEK_REGISTRY],
        default=["gamma", "vega", "theta", "delta", "vanna", "volga", "charm"],
        format_func=lambda k: GREEKS_BY_KEY[k].label)
    if keys:
        st.plotly_chart(charts.heatmap_chart(df, meta, keys, side,
                                             DEFAULT_CONVENTIONS),
                        width="stretch")


def render_model_tab(meta, mi: MarketInputs, inputs: dict) -> None:
    st.subheader("Model Details")
    detail_rows = [
        ("Model", "Black-Scholes-Merton"),
        ("Option style", "European (SPX is European, cash-settled)"),
        ("Underlying", "SPX"),
        ("Spot", f"{meta.spot:,.2f}"),
        ("Volatility", fmt_pct(mi.volatility) + " (constant across strikes)"),
        ("Risk-free rate", fmt_pct(mi.risk_free_rate)),
        ("Dividend yield", fmt_pct(mi.dividend_yield)
         + (" — assumed 0 (no input provided)" if mi.dividend_assumed else "")),
        ("Expiry", meta.expiry.strftime("%Y-%m-%d %H:%M")),
        ("T (years)", f"{meta.T:.10f}"),
        ("Day count", inputs.get("day_count", "ACT/365")
         + "  \u00b7  T = remaining_seconds / (days_per_year \u00d7 86400)"),
        ("Strike interval", f"{inputs['interval']:g}"),
        ("Strikes", f"{meta.n_strikes} ({inputs['each_side']} below + ATM + "
                    f"{inputs['each_side']} above)"),
        ("ATM strike", f"{meta.atm_strike:,.0f}"),
        ("Data source", mi.source),
    ]
    st.table(pd.DataFrame(detail_rows, columns=["Field", "Value"]))

    st.subheader("Formulas")
    st.latex(r"d_1 = \frac{\ln(S/K) + (r - q + \sigma^2/2)\,T}{\sigma\sqrt{T}}"
             r"\qquad d_2 = d_1 - \sigma\sqrt{T}")
    st.latex(r"C = S e^{-qT} N(d_1) - K e^{-rT} N(d_2) \qquad "
             r"P = K e^{-rT} N(-d_2) - S e^{-qT} N(-d_1)")
    st.latex(r"\text{Put-call parity:}\quad C - P = S e^{-qT} - K e^{-rT}")

    st.subheader("Display Conventions")
    st.markdown(
        f"""
| Greek | Convention |
|---|---|
| Theta, Charm, Color | per **calendar day** (annual value / {DEFAULT_CONVENTIONS.days_per_year:.0f}) |
| Vega, Vanna, Zomma, Rho | per **+1 percentage point** (raw \u00d7 0.01) |
| Volga | per (1 vol pct pt)\u00b2 (raw \u00d7 10\u207b\u2074) |
| Ultima | per (1 vol pct pt)\u00b3 (raw \u00d7 10\u207b\u2076) |
| Delta, Gamma, Speed | per 1 index point (unscaled) |

*Example: Vega = 12.4 means the option value changes by about $12.40 for a
+1 percentage-point change in volatility (e.g. 18.5% \u2192 19.5%), other
inputs held constant.*
""")

    with st.expander("Model Assumptions"):
        st.markdown(
            """
The Black-Scholes-Merton model assumes:

* **European exercise** (correct for SPX; no early-exercise logic applies)
* Lognormal underlying dynamics with **constant volatility**
* **Constant risk-free rate** and **continuous dividend yield**
* Continuous, frictionless trading; no transaction costs; continuous hedging
* No jumps, no stochastic volatility, unlimited liquidity

Real SPX option markets exhibit volatility smiles/skews, stochastic rates,
discrete dividends, jumps and bid/ask spreads. **BSM prices here are
theoretical model values and may differ from actual market prices.**
""")


def render_greek_guide() -> None:
    st.subheader("Greek Definitions & Conventions")
    for group in GREEK_GROUPS:
        st.markdown(f"#### {group}")
        for spec in [g for g in GREEK_REGISTRY if g.group == group]:
            with st.expander(f"{spec.label} \u2014 {spec.definition}"):
                st.markdown(f"**Formula:** `{spec.formula}`")
                st.markdown(f"**Raw unit:** {spec.raw_unit}")
                st.markdown(f"**Displayed as:** {spec.display_unit}")
                st.markdown(spec.help_text)
                if not spec.per_side:
                    st.caption("Identical for calls and puts.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    st.title(settings.APP_TITLE)
    st.markdown(
        '<div class="disclaimer">BSM outputs are <b>theoretical model '
        'values</b>. They are not guarantees of market prices, execution '
        'prices, profitability or trading outcomes. Differences vs. market '
        'arise from volatility smile/skew, stochastic rates, dividends, '
        'jumps, liquidity and bid/ask spreads.</div>',
        unsafe_allow_html=True)

    mi = sidebar_market_inputs()
    cfg, day_count = sidebar_chain_inputs()
    calculate = st.sidebar.button("CALCULATE OPTION CHAIN", type="primary",
                                  width="stretch",
                                  disabled=mi is None)

    if mi is None:
        st.info("Configure a data source in the sidebar to begin.")
        return

    T = time_to_expiry(mi.expiry, convention=day_count)

    val = validate_market_inputs(mi.spot, mi.volatility, mi.risk_free_rate,
                                 mi.dividend_yield, T)
    cfg_val = validate_chain_config(cfg.strike_interval, cfg.strikes_each_side)
    for err in val.errors + cfg_val.errors:
        st.error(err)
    if not (val.ok and cfg_val.ok):
        logger.warning("Validation failed: %s", val.errors + cfg_val.errors)
        return
    for warning in val.warnings + cfg_val.warnings:
        st.warning(warning)

    if calculate:
        st.session_state["calc"] = dict(
            spot=mi.spot, sigma=mi.volatility, r=mi.risk_free_rate,
            q=mi.dividend_yield, T=T, interval=cfg.strike_interval,
            each_side=cfg.strikes_each_side, atm_method=cfg.atm_method,
            explicit_atm=cfg.explicit_atm,
            expiry_iso=mi.expiry.isoformat(),
            source=mi.source, day_count=day_count.value,
            dividend_assumed=mi.dividend_assumed)

    if "calc" not in st.session_state:
        st.info("Set your inputs, then click **CALCULATE OPTION CHAIN**.")
        return

    p = st.session_state["calc"]
    # Render results from the calculated snapshot, not live sidebar values,
    # so KPIs/table/charts always agree with each other.
    mi_calc = MarketInputs(
        spot=p["spot"], volatility=p["sigma"], risk_free_rate=p["r"],
        dividend_yield=p["q"], expiry=datetime.fromisoformat(p["expiry_iso"]),
        source=p["source"], dividend_assumed=p["dividend_assumed"])
    try:
        df, meta = compute_chain(
            p["spot"], p["sigma"], p["r"], p["q"], p["T"], p["interval"],
            p["each_side"], p["atm_method"], p["explicit_atm"], p["expiry_iso"])
    except Exception:
        logger.exception("Chain calculation failed")
        st.error("The option chain could not be calculated with these inputs. "
                 "Please review the input values and try again.")
        return

    st.caption(f"Data source: **{p['source']}** \u00b7 Expiry "
               f"{meta.expiry.strftime('%Y-%m-%d %H:%M')} \u00b7 "
               f"Day count {p['day_count']} \u00b7 Last calculated with "
               f"\u03c3 = {p['sigma'] * 100:.2f}%, r = {p['r'] * 100:.2f}%, "
               f"q = {p['q'] * 100:.2f}%")
    if meta.is_expired:
        st.warning("This expiry is in the past: prices shown are intrinsic "
                   "values and Greeks are N/A (undefined at expiry).")

    render_kpis(meta, mi_calc, df)
    tab_chain, tab_charts, tab_heat, tab_model, tab_guide = st.tabs(
        ["Option Chain", "Charts", "Greek Heatmap", "Model Details",
         "Greek Guide"])
    with tab_chain:
        render_chain_tab(df, meta, p)
    with tab_charts:
        render_charts_tab(df, meta)
    with tab_heat:
        render_heatmap_tab(df, meta)
    with tab_model:
        render_model_tab(meta, mi_calc, p)
    with tab_guide:
        render_greek_guide()


if __name__ == "__main__":
    main()
