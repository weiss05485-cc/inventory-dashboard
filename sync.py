import pyodbc
import json
import os
from datetime import datetime
from decimal import Decimal
from config import DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD

CONN_STR = (
    f"DRIVER={{SQL Server}};"
    f"SERVER={DB_SERVER};"
    f"DATABASE={DB_NAME};"
    f"UID={DB_USER};"
    f"PWD={DB_PASSWORD};"
    f"Connection Timeout=15;"
)

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
    WITH PhysInv AS (
        SELECT ItemID, StoreID,
               ISNULL(PhysicalInventoryQty, 0) AS BaseQty,
               PhysicalInventoryDate
        FROM ItemStore WITH(NOLOCK) WHERE Status > -1
    ),
    OnHand AS (
        SELECT ItemID, StoreID, BaseQty AS Qty FROM PhysInv

        UNION ALL

        SELECT ist.ItemID, t.StoreID, te.Qty * -1
        FROM TransactionEntry te WITH(NOLOCK)
        JOIN [Transaction] t WITH(NOLOCK) ON te.TransactionID = t.TransactionID
        JOIN ItemStore ist WITH(NOLOCK) ON te.ItemStoreID = ist.ItemStoreID
        WHERE te.Status > -1 AND t.Status > -1 AND ist.Status > -1
          AND te.TransactionEntryType NOT IN (4,10,12,16)
          AND (ist.PhysicalInventoryDate IS NULL
               OR CAST(t.SaleTime AS DATE) > CAST(ist.PhysicalInventoryDate AS DATE))

        UNION ALL

        SELECT sde.ItemID, sd.StoreID,
               CASE WHEN sd.Type=2 THEN sde.Qty*-1
                    WHEN sd.Type=3 THEN sde.SentQty*-1
                    ELSE sde.Qty END
        FROM SuppliersDocsEntry sde WITH(NOLOCK)
        JOIN SuppliersDocs sd WITH(NOLOCK) ON sd.ID = sde.ID
        JOIN PhysInv pi ON pi.ItemID = sde.ItemID AND pi.StoreID = sd.StoreID
        WHERE sde.Status > 0 AND sd.Status > 0
          AND sde.Type <> 2 AND sd.Type NOT IN (5,6)
          AND (sd.DocStatus IN (7,8,9,10) OR sd.Type = 3)
          AND (pi.PhysicalInventoryDate IS NULL
               OR CAST(sd.DateT AS DATE) > CAST(pi.PhysicalInventoryDate AS DATE))

        UNION ALL

        SELECT sde.ItemID, sd.ToStoreID, sde.Qty
        FROM SuppliersDocsEntry sde WITH(NOLOCK)
        JOIN SuppliersDocs sd WITH(NOLOCK) ON sd.ID = sde.ID
        JOIN PhysInv pi ON pi.ItemID = sde.ItemID AND pi.StoreID = sd.ToStoreID
        WHERE sde.Status > 0 AND sd.Status > 0
          AND sde.Type <> 2 AND sde.Type = 5
          AND (pi.PhysicalInventoryDate IS NULL
               OR CAST(sd.DateT AS DATE) > CAST(pi.PhysicalInventoryDate AS DATE))

        UNION ALL

        SELECT dpl.ItemID, dd.StoreID,
               dpl.Quantity * CASE WHEN dd.DocType = 7 THEN 1 ELSE -1 END
        FROM DocumentPLULine dpl WITH(NOLOCK)
        JOIN DataDocument dd WITH(NOLOCK)
            ON dpl.DocNumber = dd.Number AND dpl.DocType = dd.DocType AND dpl.DocStatus = dd.Status
        JOIN PhysInv pi ON pi.ItemID = dpl.ItemID AND pi.StoreID = dd.StoreID
        WHERE dd.DocType IN (5,7,3) AND dd.Status = 1
          AND (pi.PhysicalInventoryDate IS NULL
               OR CAST(dd.DocDate AS DATE) > CAST(pi.PhysicalInventoryDate AS DATE))
    )
"""

def main():
    print("Connecting to SQL Server...")
    conn = pyodbc.connect(CONN_STR, timeout=15)
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
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
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
            CAST(ist.Price AS DECIMAL(18,2)) AS Price,
            d.Name AS Department
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ist.ReorderPoint IS NOT NULL AND ist.ReorderPoint > 0
          AND oh.Qty >= 0 AND oh.Qty <= ist.ReorderPoint
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
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE oh.Qty > 0
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
            CAST(ISNULL(ist.Price, 0) AS DECIMAL(18,2)) AS Price
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE oh.Qty > 0
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
                'p': float(row['Price'] or 0),
                's': {},
                'q': 0,
            }
        qty = float(row['Qty'] or 0)
        item_map[bc]['s'][row['StoreName']] = qty
        item_map[bc]['q'] += qty

    search_items = list(item_map.values())

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

    print(f"Done. {len(search_items)} search items | docs/data.json + search.json")
    for s in store_summary:
        print(f"  {s['StoreName']}: {s['InStock']} in stock / {int(s['TotalUnits'])} units / {int(s['StockValue']):,}")

if __name__ == "__main__":
    main()
