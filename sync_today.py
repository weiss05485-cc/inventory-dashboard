import pyodbc, json, sys
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')
from config import DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD

CONN_STR = (f"DRIVER={{SQL Server}};SERVER={DB_SERVER};DATABASE={DB_NAME};"
            f"UID={DB_USER};PWD={DB_PASSWORD};Connection Timeout=15;")
conn = pyodbc.connect(CONN_STR, timeout=15)
cur = conn.cursor()

# ── 1. מכירות היום לפי סניף ──────────────────────────────────────────────
cur.execute("""
    SELECT
        st.StoreName,
        ISNULL(SUM(t.Total), 0)  AS TotalSales,
        COUNT(DISTINCT CASE WHEN t.TransactionID IS NOT NULL THEN t.TransactionID END) AS Transactions
    FROM Store st
    LEFT JOIN [Transaction] t
        ON  t.StoreID = st.StoreID
        AND CAST(t.SaleTime AS DATE) = CAST(GETDATE() AS DATE)
        AND t.Status > -1
    WHERE st.Status = 1 AND st.Code <> '3'
    GROUP BY st.StoreID, st.StoreName
    ORDER BY st.StoreName
""")
cols = [d[0] for d in cur.description]
today_stores = []
for r in cur.fetchall():
    d = dict(zip(cols, r))
    d['TotalSales']   = round(float(d['TotalSales']), 2)
    d['Transactions'] = int(d['Transactions'])
    today_stores.append(d)

# ── 2. מכירות היום לפי מחלקה ─────────────────────────────────────────────
cur.execute("""
    SELECT
        ISNULL(d.Name, N'ללא מחלקה')  AS Dept,
        SUM(te.Total)                  AS TotalSales
    FROM TransactionEntry te
    JOIN [Transaction] t  ON te.TransactionID = t.TransactionID
    JOIN Store st         ON t.StoreID = st.StoreID AND st.Status=1 AND st.Code<>'3'
    LEFT JOIN Department d ON te.DepartmentID = d.DepartmentID
    WHERE t.Status > -1 AND te.Status > -1
      AND te.TransactionEntryType NOT IN (4,10,12,16)
      AND CAST(t.SaleTime AS DATE) = CAST(GETDATE() AS DATE)
    GROUP BY d.Name
    ORDER BY TotalSales DESC
""")
cols = [d[0] for d in cur.description]
today_depts = []
for r in cur.fetchall():
    d = dict(zip(cols, r))
    d['TotalSales'] = round(float(d['TotalSales']), 2)
    today_depts.append(d)

# ── 3. מכירות היום לפי מוכר ──────────────────────────────────────────────
cur.execute("""
    SELECT
        ISNULL(RTRIM(u.UserFName) + ' ' + RTRIM(u.UserLName), N'לא ידוע') AS SellerName,
        SUM(t.Total)                     AS TotalSales,
        COUNT(DISTINCT t.TransactionID)  AS Transactions
    FROM [Transaction] t
    JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1 AND st.Code<>'3'
    LEFT JOIN Users u ON u.UserId = t.SellerID AND u.Status=1
    WHERE t.Status > -1
      AND CAST(t.SaleTime AS DATE) = CAST(GETDATE() AS DATE)
    GROUP BY u.UserFName, u.UserLName
    ORDER BY TotalSales DESC
""")
cols = [d[0] for d in cur.description]
today_sellers = []
for r in cur.fetchall():
    d = dict(zip(cols, r))
    d['TotalSales']   = round(float(d['TotalSales']), 2)
    d['Transactions'] = int(d['Transactions'])
    today_sellers.append(d)

# ── 4. 30 ימים אחרונים — סיכום יומי ─────────────────────────────────────
cur.execute("""
    SELECT
        CONVERT(VARCHAR(10), t.SaleTime, 23)  AS SaleDate,
        ISNULL(SUM(t.Total), 0)               AS TotalSales,
        COUNT(DISTINCT t.TransactionID)       AS Transactions
    FROM [Transaction] t
    JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1 AND st.Code<>'3'
    WHERE t.Status > -1
      AND t.SaleTime >= DATEADD(DAY, -30, CAST(GETDATE() AS DATE))
    GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23)
    ORDER BY SaleDate
""")
cols = [d[0] for d in cur.description]
daily = []
for r in cur.fetchall():
    d = dict(zip(cols, r))
    d['TotalSales']   = round(float(d['TotalSales']), 2)
    d['Transactions'] = int(d['Transactions'])
    daily.append(d)

conn.close()

out = {
    'today':   today_stores,
    'depts':   today_depts,
    'sellers': today_sellers,
    'daily':   daily,
    'synced':  datetime.now().strftime('%d/%m/%Y %H:%M')
}
with open('docs/today.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False)

total = sum(s['TotalSales'] for s in today_stores)
print(f"✓ today.json — סה\"כ היום: ₪{total:,.2f} | {len(today_sellers)} מוכרים | {len(today_depts)} מחלקות")
