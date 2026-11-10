"""Deterministic Institutional Portfolio and Forensic Stress-Batch Generator.

Produces realistic multi-currency IBOR/Custodian extracts with mathematically
valid ISO 6166 Luhn Mod-10 ISINs for clean baseline runs (:3000 console) and
controlled NAV breaks (Quantity, Price, FX) plus GX-01..GX-06 schema faults.
"""

from __future__ import annotations

from src.domain.entities import compute_isin_check_digit

INSTITUTIONAL_PORTFOLIOS: tuple[str, ...] = (
    "BLK-ALADDIN-CORE",
    "BLK-CREDIT-OPP",
    "SANT-IB-RATES",
    "SANT-AM-EURO",
    "VANGUARD-INST-IDX",
)

SEED_SECURITIES: tuple[tuple[str, str, float, float, str, str, str], ...] = (
    ("US0378331005", "USD", 224.50, 1.0000, "NY-EQUITY", "EQUITY", "IRVTUS3N"),
    ("US5949181045", "USD", 415.20, 1.0000, "NY-EQUITY", "EQUITY", "IRVTUS3N"),
    ("ES0113900J37", "EUR", 4.65, 1.0850, "MADRID-RATES", "EQUITY", "BBVAESMM"),
    ("DE000BAY0017", "EUR", 28.40, 1.0850, "FRANKFURT-EQ", "EQUITY", "DEUTDEFF"),
    ("GB0002374006", "GBP", 25.10, 1.2920, "LONDON-CORE", "EQUITY", "MIDLGB22"),
    ("FR0000120271", "EUR", 58.90, 1.0850, "PARIS-CREDIT", "EQUITY", "BNPAFRPP"),
    ("NL0000235190", "EUR", 138.75, 1.0850, "AMSTERDAM-EQ", "EQUITY", "ABNANL2A"),
    ("CH0038863350", "CHF", 84.30, 1.1340, "ZURICH-WEALTH", "EQUITY", "UBSWCHZH"),
    ("JP3633400001", "JPY", 2650.00, 0.0065, "TOKYO-INDEX", "EQUITY", "BOTKJPJT"),
    ("XS2010039381", "EUR", 99.40, 1.0850, "LUX-FIXED-INC", "CORP_BOND", "CEDELULL"),
)


def _build_unique_isin(prefix: str, batch_seq: int, idx: int) -> str:
    """Generate a deterministic 12-character ISO 6166 Luhn Mod-10 valid ISIN."""
    stem = f"{prefix}{batch_seq % 100:02d}{idx:07d}"[:11]
    return f"{stem}{compute_isin_check_digit(stem)}"


class SyntheticPortfolioGenerator:
    """Deterministic generator for clean institutional batches and forensic stress feeds."""

    def generate_clean_institutional_batch(
        self,
        count: int = 50,
        valuation_date: str = "2026-11-10",
        batch_seq: int = 1,
    ) -> list[dict[str, object]]:
        """Generate `count` valid positions (RECONCILED and MINOR_TOLERANCE)."""
        records: list[dict[str, object]] = []
        used_keys: set[tuple[str, str]] = set()

        for idx in range(count):
            portfolio_id = INSTITUTIONAL_PORTFOLIOS[idx % len(INSTITUTIONAL_PORTFOLIOS)]
            base_isin, ccy, base_price, base_fx, desk, asset_cls, bic = SEED_SECURITIES[
                idx % len(SEED_SECURITIES)
            ]
            isin = base_isin
            if (portfolio_id, isin) in used_keys:
                isin = _build_unique_isin(base_isin[:2], batch_seq, idx + 1)
            used_keys.add((portfolio_id, isin))

            units = float(1000 + ((idx + 1) * 125))
            price = round(base_price + ((idx % 7) * 0.35), 4)
            fx_rate = 1.0 if ccy == "USD" else base_fx
            calc_nav = round(units * price * fx_rate, 4)

            is_minor_tol = (idx % 9 == 8) and (ccy != "USD")
            custodian_nav = round(calc_nav - 1.25, 4) if is_minor_tol else calc_nav
            abor_fx = (
                round(custodian_nav / (units * price), 6) if is_minor_tol else fx_rate
            )

            records.append(
                {
                    "position_id": f"POS-CLN-{batch_seq:02d}-{idx + 1:04d}",
                    "portfolio_id": portfolio_id,
                    "isin": isin,
                    "valuation_date": valuation_date,
                    "currency": ccy,
                    "units": units,
                    "price": price,
                    "fx_rate": fx_rate,
                    "custodian_reported_nav": custodian_nav,
                    "desk_code": desk,
                    "asset_class": asset_cls,
                    "custodian_bic": bic,
                    "abor_units": units,
                    "abor_price": price,
                    "abor_fx_rate": abor_fx,
                }
            )
        return records

    def generate_corrupt_and_nav_break_batch(
        self,
        valid_with_breaks_count: int = 15,
        schema_corrupt_count: int = 8,
        valuation_date: str = "2026-11-10",
        batch_seq: int = 1,
    ) -> list[dict[str, object]]:
        """Generate a forensic batch combining 3-factor NAV breaks and schema faults."""
        records: list[dict[str, object]] = []

        for idx in range(valid_with_breaks_count):
            portfolio_id = INSTITUTIONAL_PORTFOLIOS[idx % len(INSTITUTIONAL_PORTFOLIOS)]
            mode = idx % 3
            if mode == 0:
                isin = _build_unique_isin("US", batch_seq + 10, idx + 1)
                ccy, units, abor_u = "USD", 5000.0, 4200.0
                price = abor_p = 120.0
                fx_rate = abor_fx = 1.0
            elif mode == 1:
                isin = _build_unique_isin("DE", batch_seq + 20, idx + 1)
                ccy, units, abor_u = "EUR", 2000.0, 2000.0
                price, abor_p = 105.50, 98.20
                fx_rate = abor_fx = 1.0850
            else:
                isin = _build_unique_isin("GB", batch_seq + 30, idx + 1)
                ccy, units, abor_u = "GBP", 10000.0, 10000.0
                price = abor_p = 50.0
                fx_rate, abor_fx = 1.2950, 1.2650

            custodian_nav = round(abor_u * abor_p * abor_fx, 4)
            records.append(
                {
                    "position_id": f"POS-BRK-{batch_seq:02d}-{idx + 1:04d}",
                    "portfolio_id": portfolio_id,
                    "isin": isin,
                    "valuation_date": valuation_date,
                    "currency": ccy,
                    "units": units,
                    "price": price,
                    "fx_rate": fx_rate,
                    "custodian_reported_nav": custodian_nav,
                    "desk_code": "FORENSIC-DESK",
                    "asset_class": "INSTITUTIONAL",
                    "custodian_bic": "IRVTUS3N",
                    "abor_units": abor_u,
                    "abor_price": abor_p,
                    "abor_fx_rate": abor_fx,
                }
            )

        corrupt_templates: list[dict[str, object]] = []
        if schema_corrupt_count >= 2:
            dup_isin = _build_unique_isin("LU", batch_seq + 90, 999)
            for dup_i in (1, 2):
                corrupt_templates.append(
                    {
                        "position_id": f"POS-DUP-{batch_seq:02d}-{dup_i:02d}",
                        "portfolio_id": "BLK-ALADDIN-CORE",
                        "isin": dup_isin,
                        "valuation_date": valuation_date,
                        "currency": "EUR",
                        "units": 500.0 * dup_i,
                        "price": 100.0,
                        "fx_rate": 1.085,
                        "custodian_reported_nav": 54250.0 * dup_i,
                    }
                )

        remaining = schema_corrupt_count - len(corrupt_templates)
        fault_builders = (
            {"isin": "US0378331009"},
            {"currency": "USDT"},
            {"price": -42.50},
            {"price": float("nan")},
            {"units": float("inf")},
            {"currency": "USD", "fx_rate": 1.15},
            {"currency": "EURO"},
        )
        for f_idx in range(remaining):
            base_fault: dict[str, object] = {
                "position_id": f"POS-ERR-{batch_seq:02d}-{f_idx + 1:04d}",
                "portfolio_id": INSTITUTIONAL_PORTFOLIOS[f_idx % len(INSTITUTIONAL_PORTFOLIOS)],
                "isin": _build_unique_isin("FR", batch_seq + 50, f_idx + 1),
                "valuation_date": valuation_date,
                "currency": "EUR",
                "units": 100.0,
                "price": 50.0,
                "fx_rate": 1.085,
                "custodian_reported_nav": 5425.0,
            }
            base_fault.update(fault_builders[f_idx % len(fault_builders)])
            corrupt_templates.append(base_fault)

        return records + corrupt_templates[:schema_corrupt_count]
