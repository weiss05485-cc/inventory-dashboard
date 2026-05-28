import json
import os
import re
from datetime import datetime
from decimal import Decimal

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
    _conn = pyodbc.connect(CONN_STR, timeout=15)
except Exception:
    import pymssql
    _conn = pymssql.connect(server=DB_SERVER, user=DB_USER,
                            password=DB_PASSWORD, database=DB_NAME,
                            timeout=15, login_timeout=15)

BARCODE_RE = re.compile(r'^(\d{2})([A-Za-z]+)(\d{2})([A-Za-z0-9]{2})(.+)$', re.IGNORECASE)

def extract_size(barcode):
    m = BARCODE_RE.match(str(barcode or '').strip())
    return m.group(5) if m else ''

def q(cur, sql, params=None):
    cur.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def serial(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%d %H:%M")
    return str(obj)

ONHAND_CTE = """
    WITH Dec31Inv AS (
        -- Latest completed physical inventory per store on 31/12/2025
        SELECT pi.StoreID, pi.PhysicalInventoryID,
               ROW_NUMBER() OVER (PARTITION BY pi.StoreID ORDER BY pi.OpenTime DESC) AS rn
        FROM PhysicalInventory pi
        WHERE CAST(pi.OpenTime AS DATE) = CAST(N'2025-12-31' AS DATE) AND pi.Status = 2
    ),
    Dec31Baseline AS (
        -- Items that WERE counted on Dec 31: QuantityCounted is the baseline
        SELECT pie.ItemID, pie.StoreID, SUM(pie.QuantityCounted) AS Qty
        FROM PhysicalInventoryEntry pie
        JOIN Dec31Inv d ON d.PhysicalInventoryID = pie.PhysicalInventoryID AND d.rn = 1
        GROUP BY pie.ItemID, pie.StoreID
    ),
    RawMov AS (
        -- Baseline: Dec 31 physical count
        SELECT ItemID, StoreID, Qty FROM Dec31Baseline

        UNION ALL

        -- Sales from Dec 31 — only for items that were counted on Dec 31
        SELECT ist.ItemID, t.StoreID, te.Qty * -1
        FROM TransactionEntry te
        JOIN [Transaction] t ON te.TransactionID = t.TransactionID
        JOIN ItemStore ist ON ist.ItemStoreID = te.ItemStoreID
        JOIN Dec31Baseline db ON db.ItemID = ist.ItemID AND db.StoreID = t.StoreID
        WHERE te.Status > -1 AND t.Status > -1 AND ist.Status > -1
          AND te.TransactionEntryType NOT IN (4, 10, 12, 16)
          AND t.SaleTime >= CAST(N'2025-12-31' AS DATE)

        UNION ALL

        -- Supplier receipts from Dec 31 — Dec31 items only
        SELECT sde.ItemID, sd.StoreID,
               (CASE WHEN sd.Type = 2 THEN sde.Qty * -1
                     WHEN sd.Type = 3 THEN sde.SentQty * -1
                     ELSE sde.Qty END)
        FROM SuppliersDocsEntry sde
        JOIN SuppliersDocs sd ON sd.ID = sde.ID
        JOIN Dec31Baseline db ON db.ItemID = sde.ItemID AND db.StoreID = sd.StoreID
        WHERE sde.Status > 0 AND sd.Status > 0 AND sde.Type <> 2
          AND sd.Type NOT IN (5, 6) AND sd.DocStatus IN (7, 8, 9, 10)
          AND sd.DateT >= CAST(N'2025-12-31' AS DATE)

        UNION ALL

        -- Transfer receipts from Dec 31 — Dec31 items only
        SELECT sde.ItemID, sd.ToStoreID, sde.Qty
        FROM SuppliersDocsEntry sde
        JOIN SuppliersDocs sd ON sd.ID = sde.ID
        JOIN Dec31Baseline db ON db.ItemID = sde.ItemID AND db.StoreID = sd.ToStoreID
        WHERE sde.Status > 0 AND sd.Status > 0 AND sde.Type = 5
          AND sd.DateT >= CAST(N'2025-12-31' AS DATE)

        UNION ALL

        -- Deliveries / returns from Dec 31 — Dec31 items only
        SELECT dpl.ItemID, dd.StoreID,
               dpl.Quantity * (CASE WHEN dd.DocType = 7 THEN 1 ELSE -1 END)
        FROM DocumentPLULine dpl
        JOIN DataDocument dd ON dpl.DocNumber = dd.Number
                             AND dpl.DocType = dd.DocType
                             AND dpl.DocStatus = dd.Status
        JOIN Dec31Baseline db ON db.ItemID = dpl.ItemID AND db.StoreID = dd.StoreID
        WHERE dd.DocType IN (5, 7, 3) AND dd.Status = 1
          AND dd.DocDate >= CAST(N'2025-12-31' AS DATE)
    ),
    OnHand AS (
        -- Items IN Dec 31 inventory: Dec 31 count + movements from Dec 31
        SELECT ItemID, StoreID, SUM(Qty) AS Qty
        FROM RawMov
        GROUP BY ItemID, StoreID

        UNION ALL

        -- Items NOT in Dec 31 inventory: fall back to original view
        SELECT v.ItemID, v.StoreID, SUM(v.Qty) AS Qty
        FROM ItemMovementForQuickOnHandView v
        WHERE NOT EXISTS (
            SELECT 1 FROM Dec31Baseline db
            WHERE db.ItemID = v.ItemID AND db.StoreID = v.StoreID
        )
        GROUP BY v.ItemID, v.StoreID
    )
"""

def main():
    print("Connecting to SQL Server...")
    conn = _conn
    cur = conn.cursor()

    print("  Store summary...")
    store_summary = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            st.StoreName,
            st.Code AS StoreCode,
            COUNT(*) AS TotalSKUs,
            SUM(CASE WHEN oh.Qty > 0  THEN 1 ELSE 0 END) AS InStock,
            SUM(CASE WHEN oh.Qty = 0  THEN 1 ELSE 0 END) AS ZeroStock,
            SUM(CASE WHEN ist.ReorderPoint IS NOT NULL AND oh.Qty >= 0
                      AND oh.Qty <= ist.ReorderPoint AND ist.ReorderPoint > 0
                      THEN 1 ELSE 0 END) AS LowStock,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty ELSE 0 END) AS DECIMAL(18,1)) AS TotalUnits,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty * ISNULL(ist.AVGCost, 0) ELSE 0 END)
                 AS DECIMAL(18,0)) AS StockValue
        FROM OnHand oh
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1 AND st.Code <> '3'
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
        GROUP BY st.StoreID, st.StoreName, st.Code, st.Sort
        ORDER BY st.Sort
    """)

    print("  Low stock items...")
    low_stock = q(cur, f"""
        {ONHAND_CTE}
        SELECT TOP 300
            im.Name, im.BarcodeNumber, im.ModelNumber,
            st.StoreName,
            CAST(oh.Qty AS DECIMAL(18,1)) AS OnHand,
            CAST(ist.ReorderPoint AS DECIMAL(18,1)) AS ReorderPoint,
            CAST(COALESCE(NULLIF(ist.AVCostWithoutTax, 0), NULLIF(ist.CostWithoutTax, 0), NULLIF(ist.AVGCost / 1.18, 0)) AS DECIMAL(18,2)) AS Price,
            d.Name AS Department
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1 AND st.Code <> '3'
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ist.ReorderPoint IS NOT NULL AND ist.ReorderPoint > 0
          AND oh.Qty >= 0 AND oh.Qty <= ist.ReorderPoint
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
        ORDER BY (oh.Qty - ist.ReorderPoint) ASC, st.Sort
    """)

    print("  Department breakdown...")
    by_department = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            ISNULL(d.Name, N'ללא מחלקה') AS Department,
            st.StoreName,
            SUM(CASE WHEN oh.Qty > 0 THEN 1 ELSE 0 END) AS InStock,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty ELSE 0 END) AS DECIMAL(18,1)) AS TotalUnits,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty * ISNULL(ist.AVGCost, 0) ELSE 0 END)
                 AS DECIMAL(18,0)) AS Value
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1 AND st.Code <> '3'
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE oh.Qty > 0
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
        GROUP BY d.Name, st.StoreName, st.Sort
        HAVING SUM(oh.Qty) > 0
        ORDER BY SUM(oh.Qty * ISNULL(ist.AVGCost, 0)) DESC
    """)

    print("  Search items...")
    flat_items = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            im.Name,
            im.BarcodeNumber,
            im.ModelNumber,
            st.StoreName,
            CAST(oh.Qty AS DECIMAL(18,1)) AS Qty,
            d.Name AS Department,
            CAST(COALESCE(NULLIF(ist.AVCostWithoutTax, 0), NULLIF(ist.CostWithoutTax, 0), NULLIF(ist.AVGCost / 1.18, 0)) AS DECIMAL(18,2)) AS Price
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1 AND st.Code <> '3'
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE oh.Qty > 0
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
    """)

    # Group by barcode → one item with per-store quantities
    item_map = {}
    for row in flat_items:
        bc = str(row['BarcodeNumber'] or '').strip() or row['Name']
        if bc not in item_map:
            item_map[bc] = {
                'n': row['Name'],
                'b': row['BarcodeNumber'] or '',
                'mn': row['ModelNumber'] or '',
                'd': row['Department'] or '',
                'p': 0,
                's': {},
                'q': 0,
            }
        qty   = float(row['Qty'] or 0)
        price = float(row['Price'] or 0)
        item_map[bc]['s'][row['StoreName']] = qty
        item_map[bc]['q'] += qty
        # שמור את המחיר הגבוה ביותר שנמצא (כמה סניפים — עלות שונה; חלקם 0)
        if price > item_map[bc]['p']:
            item_map[bc]['p'] = price

    # --- מילוי מחירים חסרים לפי ברקוד ---
    # ברקוד: YY + עונה + דגם + צבע + מידה  (e.g. 25W0303M40)
    # רמה 1 — מפתח מלא (שנה+עונה+דגם+צבע): אותה שנה, אותו דגם וצבע
    # רמה 2 — מפתח ללא שנה (עונה+דגם+צבע): אותו דגם גם משנים אחרות
    # רמה 1: שנה+עונה+דגם+צבע  (25W0303)
    # רמה 2: עונה+דגם+צבע ללא שנה  (W0303)
    # רמה 3: דגם+צבע בלבד  (0303) — ללא שנה ועונה
    MODEL_FULL_RE    = re.compile(r'^(\d{2}[A-Za-z]+\d{2}[A-Za-z0-9]{2})', re.IGNORECASE)
    MODEL_NOYEAR_RE  = re.compile(r'^\d{2}([A-Za-z]+\d{2}[A-Za-z0-9]{2})', re.IGNORECASE)
    MODEL_NOSEAS_RE  = re.compile(r'^\d{2}[A-Za-z]+(\d{2}[A-Za-z0-9]{2})',  re.IGNORECASE)

    def build_price_map(key_re):
        pm = {}
        for item in item_map.values():
            if item['p'] > 0 and item['b']:
                m = key_re.match(str(item['b']).strip())
                if m and item['p'] > pm.get(m.group(1), 0):
                    pm[m.group(1)] = item['p']
        return pm

    price_full   = build_price_map(MODEL_FULL_RE)
    price_noyear = build_price_map(MODEL_NOYEAR_RE)
    price_noseas = build_price_map(MODEL_NOSEAS_RE)

    filled = 0
    for item in item_map.values():
        if item['p'] == 0 and item['b']:
            bc = str(item['b']).strip()
            m1 = MODEL_FULL_RE.match(bc)
            m2 = MODEL_NOYEAR_RE.match(bc)
            m3 = MODEL_NOSEAS_RE.match(bc)
            p = (price_full.get(m1.group(1))   if m1 else None) or \
                (price_noyear.get(m2.group(1))  if m2 else None) or \
                (price_noseas.get(m3.group(1))  if m3 else None)
            if p:
                item['p'] = p
                filled += 1
    print(f"  Price fill from barcode model: {filled} items filled")

    search_items = list(item_map.values())

    print("  Sales by group/size/month...")
    sales_raw = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            im.Name,
            im.BarcodeNumber,
            ISNULL(d.Name, N'ללא מחלקה') AS Department,
            ISNULL(ig.ItemGroupName, N'ללא קבוצה') AS GroupName,
            st.StoreName,
            CONVERT(VARCHAR(7), t.SaleTime, 120) AS YearMonth,
            SUM(te.Qty) AS QtySold
        FROM TransactionEntry te
        JOIN [Transaction] t ON te.TransactionID = t.TransactionID
        JOIN ItemStore ist ON ist.ItemStoreID = te.ItemStoreID
        JOIN ItemMain im ON im.ItemID = ist.ItemID AND im.Status = 1
        JOIN Store st ON t.StoreID = st.StoreID AND st.Status = 1 AND st.Code <> '3'
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        LEFT JOIN (
            SELECT ItemID, ItemGroupID,
                   ROW_NUMBER() OVER (PARTITION BY ItemID ORDER BY CASE WHEN IsMainGroup=1 THEN 0 ELSE 1 END) AS rn
            FROM ItemToGroup WHERE Status = 1
        ) itg ON itg.ItemID = im.ItemID AND itg.rn = 1
        LEFT JOIN ItemGroup ig ON ig.ItemGroupID = itg.ItemGroupID AND ig.Status = 1
        WHERE te.Status > -1 AND t.Status > -1 AND ist.Status > -1
          AND te.TransactionEntryType NOT IN (4, 10, 12, 16)
          AND t.SaleTime >= DATEADD(MONTH, -13, GETDATE())
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
        GROUP BY im.Name, im.BarcodeNumber, d.Name, ig.ItemGroupName,
                 st.StoreName, CONVERT(VARCHAR(7), t.SaleTime, 120)
    """)

    # Process sales: extract size & model code from barcode
    sales_map = {}
    for row in sales_raw:
        bc = str(row['BarcodeNumber'] or '').strip()
        m = BARCODE_RE.match(bc)
        size = m.group(5) if m else ''
        mc   = m.group(4) if m else ''
        key = (row['YearMonth'], row['Department'], row['GroupName'],
               row['Name'].strip(), mc, size, row['StoreName'])
        if key not in sales_map:
            sales_map[key] = 0.0
        sales_map[key] += float(row['QtySold'] or 0)

    sales_items = [
        {'ym': ym, 'dept': dept, 'g': group, 'n': name,
         'mc': mc, 'sz': size, 'st': store, 'q': round(qty)}
        for (ym, dept, group, name, mc, size, store), qty in sales_map.items()
        if qty > 0
    ]

    print("  Reports by group/size...")
    report_raw = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            im.Name,
            im.BarcodeNumber,
            ISNULL(d.Name, N'ללא מחלקה') AS Department,
            ISNULL(ig.ItemGroupName, N'ללא קבוצה') AS GroupName,
            st.StoreName,
            CAST(SUM(oh.Qty) AS DECIMAL(18,1)) AS Qty
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1 AND st.Code <> '3'
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        LEFT JOIN (
            SELECT ItemID, ItemGroupID,
                   ROW_NUMBER() OVER (PARTITION BY ItemID ORDER BY CASE WHEN IsMainGroup=1 THEN 0 ELSE 1 END) AS rn
            FROM ItemToGroup WHERE Status = 1
        ) itg ON itg.ItemID = im.ItemID AND itg.rn = 1
        LEFT JOIN ItemGroup ig ON ig.ItemGroupID = itg.ItemGroupID AND ig.Status = 1
        WHERE oh.Qty >= 0
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
        GROUP BY im.Name, im.BarcodeNumber, d.Name, ig.ItemGroupName, st.StoreName, st.Sort
        ORDER BY st.Sort
    """)

    # Aggregate by (dept, group, name, size) — combine multiple barcodes/years
    report_map = {}
    for row in report_raw:
        bc = str(row['BarcodeNumber'] or '').strip()
        m = BARCODE_RE.match(bc)
        size = m.group(5) if m else ''
        mc   = m.group(4) if m else ''
        key = (row['Department'], row['GroupName'], row['Name'].strip(), mc, size)
        if key not in report_map:
            report_map[key] = {'stores': {}, 'total': 0.0}
        qty = float(row['Qty'] or 0)
        store = row['StoreName']
        report_map[key]['stores'][store] = report_map[key]['stores'].get(store, 0.0) + qty
        report_map[key]['total'] += qty

    report_items = [
        {'dept': dept, 'g': group, 'n': name, 'mc': mc, 'sz': size,
         's': data['stores'], 'q': data['total']}
        for (dept, group, name, mc, size), data in report_map.items()
    ]

    conn.close()

    os.makedirs("docs", exist_ok=True)

    main_data = {
        "last_updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "store_summary": store_summary,
        "low_stock": low_stock,
        "by_department": by_department,
    }
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(main_data, f, ensure_ascii=False, default=serial)

    with open("docs/search.json", "w", encoding="utf-8") as f:
        json.dump(search_items, f, ensure_ascii=False, default=serial)

    with open("docs/reports.json", "w", encoding="utf-8") as f:
        json.dump(report_items, f, ensure_ascii=False, default=serial)

    with open("docs/sales.json", "w", encoding="utf-8") as f:
        json.dump(sales_items, f, ensure_ascii=False, default=serial)

    print(f"Done. {len(search_items)} search | {len(report_items)} report | {len(sales_items)} sales rows")
    for s in store_summary:
        print(f"  {s['StoreName']}: {s['InStock']} in stock / {int(s['TotalUnits'])} units / {int(s['StockValue']):,}")

if __name__ == "__main__":
    main()
