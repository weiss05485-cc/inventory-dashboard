import json, sys, os
from datetime import datetime
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')

# credentials מ-config.py (מקומי) או environment variables (GitHub Actions)
try:
    from config import DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD
except ImportError:
    DB_SERVER   = os.environ['DB_SERVER']
    DB_NAME     = os.environ['DB_NAME']
    DB_USER     = os.environ['DB_USER']
    DB_PASSWORD = os.environ['DB_PASSWORD']

# חיבור: pyodbc על Windows, pymssql על Linux (GitHub Actions)
try:
    import pyodbc
    CONN_STR = (f"DRIVER={{SQL Server}};SERVER={DB_SERVER};DATABASE={DB_NAME};"
                f"UID={DB_USER};PWD={DB_PASSWORD};Connection Timeout=15;")
    conn = pyodbc.connect(CONN_STR, timeout=15)
except Exception:
    import pymssql
    conn = pymssql.connect(server=DB_SERVER, user=DB_USER,
                           password=DB_PASSWORD, database=DB_NAME,
                           timeout=15, login_timeout=15)
cur = conn.cursor()

# ── 1. לפי סניף × יום (כל ההיסטוריה) ────────────────────────────────────
print("שולף נתוני סניף...")
cur.execute("""
    SELECT
        CONVERT(VARCHAR(10), t.SaleTime, 23)  AS SaleDate,
        st.StoreName,
        ISNULL(SUM(t.Total), 0)               AS TotalSales,
        COUNT(DISTINCT t.TransactionID)       AS Transactions
    FROM [Transaction] t
    JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1 AND st.Code<>'3'
    WHERE t.Status > -1
      AND t.TransactionType NOT IN (14, 21)
    GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23), st.StoreID, st.StoreName
    ORDER BY SaleDate, st.StoreName
""")
cols = [d[0] for d in cur.description]
stores_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
for r in stores_raw:
    r['TotalSales'] = round(float(r['TotalSales']), 2)
    r['Transactions'] = int(r['Transactions'])
print(f"  {len(stores_raw)} שורות סניף")

# ── 2. לפי מחלקה × יום (כל ההיסטוריה) ──────────────────────────────────
print("שולף נתוני מחלקה...")
cur.execute("""
    SELECT
        CONVERT(VARCHAR(10), t.SaleTime, 23)  AS SaleDate,
        ISNULL(d.Name, N'ללא מחלקה')          AS Dept,
        SUM(te.Total)                          AS TotalSales
    FROM TransactionEntry te
    JOIN [Transaction] t  ON te.TransactionID = t.TransactionID
    JOIN Store st         ON t.StoreID = st.StoreID AND st.Status=1 AND st.Code<>'3'
    LEFT JOIN Department d ON te.DepartmentID = d.DepartmentID
    WHERE t.Status > -1 AND te.Status > -1
      AND te.TransactionEntryType NOT IN (4,10,12,16)
      AND t.TransactionType NOT IN (14, 21)
    GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23), d.Name
    ORDER BY SaleDate, TotalSales DESC
""")
cols = [d[0] for d in cur.description]
depts_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
for r in depts_raw:
    r['TotalSales'] = round(float(r['TotalSales']), 2)
print(f"  {len(depts_raw)} שורות מחלקה")

# ── 3. לפי מוכר × יום (כל ההיסטוריה) ───────────────────────────────────
print("שולף נתוני מוכרים...")
cur.execute("""
    SELECT
        CONVERT(VARCHAR(10), t.SaleTime, 23)                           AS SaleDate,
        ISNULL(RTRIM(u.UserFName)+' '+RTRIM(u.UserLName), N'לא ידוע') AS SellerName,
        SUM(t.Total)                                                    AS TotalSales,
        COUNT(DISTINCT t.TransactionID)                                 AS Transactions
    FROM [Transaction] t
    JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1 AND st.Code<>'3'
    LEFT JOIN Users u ON u.UserId = t.SellerID AND u.Status=1
    WHERE t.Status > -1
      AND t.TransactionType NOT IN (14, 21)
    GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23), u.UserFName, u.UserLName
    ORDER BY SaleDate, TotalSales DESC
""")
cols = [d[0] for d in cur.description]
sellers_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
for r in sellers_raw:
    r['TotalSales'] = round(float(r['TotalSales']), 2)
    r['Transactions'] = int(r['Transactions'])
print(f"  {len(sellers_raw)} שורות מוכרים")

# ── 4. סיכום יומי (לגרף) ─────────────────────────────────────────────────
print("שולף סיכום יומי...")
cur.execute("""
    SELECT
        CONVERT(VARCHAR(10), t.SaleTime, 23)  AS SaleDate,
        ISNULL(SUM(t.Total), 0)               AS TotalSales,
        COUNT(DISTINCT t.TransactionID)       AS Transactions
    FROM [Transaction] t
    JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1 AND st.Code<>'3'
    WHERE t.Status > -1
      AND t.TransactionType NOT IN (14, 21)
    GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23)
    ORDER BY SaleDate
""")
cols = [d[0] for d in cur.description]
daily = []
for r in cur.fetchall():
    d = dict(zip(cols, r))
    d['TotalSales'] = round(float(d['TotalSales']), 2)
    d['Transactions'] = int(d['Transactions'])
    daily.append(d)
print(f"  {len(daily)} ימים")

# ── 5. אמצעי תשלום × יום (כל ההיסטוריה) ─────────────────────────────────
print("שולף נתוני אמצעי תשלום...")
cur.execute("""
    SELECT
        CONVERT(VARCHAR(10), t.SaleTime, 23)                         AS SaleDate,
        ISNULL(tn.TenderNameHe, CAST(te.TenderID AS NVARCHAR(10)))   AS PayMethod,
        SUM(te.Amount)                                                AS TotalAmount,
        COUNT(*)                                                      AS Cnt
    FROM TenderEntry te
    JOIN [Transaction] t  ON te.TransactionID = t.TransactionID
    JOIN Store st         ON t.StoreID = st.StoreID AND st.Status=1 AND st.Code<>'3'
    LEFT JOIN Tender tn   ON te.TenderID = tn.TenderID
    WHERE t.Status > -1
      AND t.TransactionType NOT IN (14, 21)
      AND te.Status > -1
    GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23), te.TenderID, tn.TenderNameHe
    ORDER BY SaleDate, TotalAmount DESC
""")
cols = [d[0] for d in cur.description]
payments_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
for r in payments_raw:
    r['TotalAmount'] = round(float(r['TotalAmount']), 2)
    r['Cnt'] = int(r['Cnt'])
print(f"  {len(payments_raw)} שורות תשלומים")

conn.close()

# ── ארגון לפי תאריך ───────────────────────────────────────────────────────
by_date = defaultdict(lambda: {'stores': [], 'depts': [], 'sellers': [], 'payments': []})
for r in stores_raw:
    dt = r.pop('SaleDate')
    by_date[dt]['stores'].append(r)
for r in depts_raw:
    dt = r.pop('SaleDate')
    by_date[dt]['depts'].append(r)
for r in sellers_raw:
    dt = r.pop('SaleDate')
    by_date[dt]['sellers'].append(r)
for r in payments_raw:
    dt = r.pop('SaleDate')
    by_date[dt]['payments'].append(r)

today_str = datetime.now().strftime('%Y-%m-%d')
out = {
    'today':   today_str,
    'synced':  datetime.now().strftime('%d/%m/%Y %H:%M'),
    'daily':   daily,
    'by_date': dict(by_date)
}
print("שומר today.json...")
with open('docs/today.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False)

today_total = sum(r['TotalSales'] for r in (by_date.get(today_str, {}).get('stores') or []))
print(f"✓ today.json — {len(by_date)} ימים | היום: ₪{today_total:,.2f}")
