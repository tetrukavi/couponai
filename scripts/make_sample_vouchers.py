"""Generate a dummy voucher spreadsheet at data/vouchers/my_vouchers.xlsx.

All codes and amounts are fake. Dates are relative to today, so the sample always contains
both valid and expired vouchers.
"""

from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

OUT = Path(__file__).resolve().parent.parent / "data" / "vouchers" / "my_vouchers.xlsx"

COLUMNS = [
    "voucher_id",
    "issuer",
    "card_network",
    "valid_stores",
    "code",
    "face_value",
    "paid_amount",
    "remaining_balance",
    "currency",
    "purchase_date",
    "expiry_date",
    "status",
    "purchased_from",
    "notes",
]

T = date.today()
d = lambda days: T + timedelta(days=days)  # noqa: E731

ROWS = [
    # Direct store vouchers
    [
        "V001",
        "Foot Locker",
        "",
        "Foot Locker;פוט לוקר",
        "FL-DEMO-1001",
        200,
        170,
        200,
        "ILS",
        d(-60),
        d(300),
        "unused",
        "Credit card club",
        "15% off via a club",
    ],
    [
        "V002",
        "Foot Locker",
        "",
        "Foot Locker",
        "FL-DEMO-1002",
        100,
        85,
        40,
        "ILS",
        d(-200),
        d(120),
        "partial",
        "Bank club",
        "partly used",
    ],
    [
        "V003",
        "Nike",
        "",
        "Nike;נייקי",
        "NK-DEMO-2001",
        300,
        255,
        300,
        "ILS",
        d(-30),
        d(330),
        "unused",
        "Workplace club",
        "",
    ],
    [
        "V004",
        "Castro",
        "",
        "קסטרו;Castro",
        "CS-DEMO-3001",
        150,
        120,
        150,
        "ILS",
        d(-400),
        d(-10),
        "unused",
        "Credit card club",
        "EXPIRED: should be filtered",
    ],
    [
        "V005",
        "Super-Pharm",
        "",
        "סופר-פארם;Super-Pharm",
        "SP-DEMO-4001",
        100,
        90,
        0,
        "ILS",
        d(-90),
        d(200),
        "used",
        "Bank club",
        "fully used: should be filtered",
    ],
    # Multi-store card networks
    [
        "V006",
        "BuyMe",
        "BuyMe ALL",
        "",
        "BM-DEMO-5001",
        250,
        250,
        250,
        "ILS",
        d(-15),
        d(1800),
        "unused",
        "Gift (birthday)",
        "network card",
    ],
    [
        "V007",
        "Max",
        "Max Gift Card",
        "",
        "MX-DEMO-6001",
        500,
        450,
        320,
        "ILS",
        d(-100),
        d(900),
        "partial",
        "Max",
        "10% discount on purchase",
    ],
    [
        "V008",
        "Tav Hazahav",
        "תו הזהב",
        "",
        "TZ-DEMO-7001",
        200,
        180,
        200,
        "ILS",
        d(-20),
        d(1000),
        "unused",
        "Workplace club",
        "",
    ],
    [
        "V009",
        "BuyMe",
        "BuyMe Fashion",
        "",
        "BM-DEMO-5002",
        150,
        150,
        150,
        "ILS",
        d(-5),
        d(1800),
        "unused",
        "Gift (work)",
        "fashion & sport stores",
    ],
    # Misc
    [
        "V010",
        "Adidas",
        "",
        "Adidas;אדידס",
        "AD-DEMO-8001",
        200,
        160,
        200,
        "ILS",
        d(-10),
        d(25),
        "unused",
        "Credit card club",
        "expires soon",
    ],
    [
        "V011",
        "Shufersal",
        "",
        "שופרסל;Shufersal",
        "SH-DEMO-9001",
        400,
        370,
        400,
        "ILS",
        d(-45),
        d(300),
        "unused",
        "Workplace club",
        "",
    ],
    [
        "V012",
        "Foot Locker",
        "",
        "פוט לוקר",
        "FL-DEMO-1003",
        100,
        100,
        100,
        "ILS",
        d(-500),
        d(-30),
        "unused",
        "Gift",
        "EXPIRED: should be filtered",
    ],
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "vouchers"
    ws.append(COLUMNS)
    for row in ROWS:
        ws.append(row)

    header_fill = PatternFill("solid", fgColor="1F2937")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    for col_idx, name in enumerate(COLUMNS, start=1):
        letter = get_column_letter(col_idx)
        width = max(len(name), *(len(str(r[col_idx - 1])) for r in ROWS)) + 2
        ws.column_dimensions[letter].width = min(width, 40)
        if name in ("purchase_date", "expiry_date"):
            for cell in ws[letter][1:]:
                cell.number_format = "yyyy-mm-dd"
    ws.freeze_panes = "A2"
    wb.save(OUT)
    print(f"Wrote {len(ROWS)} vouchers to {OUT}")


if __name__ == "__main__":
    main()
