# -*- coding: utf-8 -*-
"""
סריקת מבנה (schema) בלבד — לאיתור טבלאות/עמודות של מבצעים/הנחות בארנט.
קריאה בלבד. מדפיס רק שמות טבלאות ועמודות (ללא נתונים) — בטוח ללוגים ציבוריים.
"""
import os
try:
    from config import DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD
except ImportError:
    DB_SERVER   = os.environ['DB_SERVER']
    DB_NAME     = os.environ['DB_NAME']
    DB_USER     = os.environ['DB_USER']
    DB_PASSWORD = os.environ['DB_PASSWORD']

def _connect():
    try:
        import pyodbc
        return pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={DB_SERVER};DATABASE={DB_NAME};"
            f"UID={DB_USER};PWD={DB_PASSWORD};Connection Timeout=15;", timeout=15)
    except Exception:
        import pymssql
        return pymssql.connect(server=DB_SERVER, user=DB_USER, password=DB_PASSWORD,
                               database=DB_NAME, timeout=15, login_timeout=15)

KEYWORDS = ['sale', 'discount', 'promo', 'coupon', 'benefit', 'deal',
            'offer', 'mivz', 'mivt', 'campaign', 'reward', 'bonus', 'voucher']

def main():
    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE'")
    tables = sorted(r[0] for r in cur.fetchall())
    print(f"=== TOTAL TABLES: {len(tables)} ===")

    matching = [t for t in tables if any(k in t.lower() for k in KEYWORDS)]
    print(f"=== PROMO-RELATED TABLES ({len(matching)}) ===")
    for t in matching:
        print("  TABLE:", t)

    for t in matching:
        cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_NAME = '{t}' ORDER BY ORDINAL_POSITION")
        print(f"--- COLUMNS of {t} ---")
        for c in cur.fetchall():
            print("    ", c[0], "|", c[1])

    cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS")
    print("=== COLUMNS matching keywords (across all tables) ===")
    for tn, cn in sorted(cur.fetchall()):
        if any(k in cn.lower() for k in KEYWORDS):
            print("    ", tn, ".", cn)

    print("=== DONE ===")

if __name__ == "__main__":
    main()
