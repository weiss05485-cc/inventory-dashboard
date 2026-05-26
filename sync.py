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

# Real inventory = SUM(ItemMovementForQuickOnHandView) per item+store
ONHAND_CTE = """
    WITH OnHand AS (
        SELECT v.ItemID, v.StoreID, SUM(v.Qty) AS Qty
        FROM ItemMovementForQuickOnHandView v
        GROUP BY v.ItemID, v.StoreID
    )
"""

def main():
    print("Connecting to SQL Server...")
    conn = pyodbc.connect(CONN_STR, timeout=15)
    cur = conn.cursor()

    # Store summary
    print("  Store summary...")
    store_summary = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            st.StoreName,
            st.Code AS StoreCode,
            COUNT(*)                                                            AS TotalSKUs,
            SUM(CASE WHEN oh.Qty > 0  THEN 1 ELSE 0 END)                      AS InStock,
            SUM(CASE WHEN oh.Qty = 0  THEN 1 ELSE 0 END)                      AS ZeroStock,
            SUM(CASE WHEN oh.Qty < 0  THEN 1 ELSE 0 END)                      AS NegativeStock,
            SUM(CASE WHEN ist.ReorderPoint IS NOT NULL AND oh.Qty >= 0
                      AND oh.Qty <= ist.ReorderPoint AND ist.ReorderPoint > 0
                      THEN 1 ELSE 0 END)                                       AS LowStock,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty ELSE 0 END) AS DECIMAL(18,1)) AS TotalUnits,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty * ISNULL(ist.AVGCost, 0) ELSE 0 END)
                 AS DECIMAL(18,0))                                             AS StockValue
        FROM OnHand oh
        JOIN Store st  ON oh.StoreID = st.StoreID AND st.Status = 1
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        GROUP BY st.StoreID, st.StoreName, st.Code, st.Sort
        ORDER BY st.Sort
    """)

    # Low stock items
    print("  Low stock items...")
    low_stock = q(cur, f"""
        {ONHAND_CTE}
        SELECT TOP 200
            im.Name, im.BarcodeNumber, im.ModelNumber,
            st.StoreName,
            CAST(oh.Qty              AS DECIMAL(18,1)) AS OnHand,
            CAST(ist.ReorderPoint    AS DECIMAL(18,1)) AS ReorderPoint,
            CAST(ist.Price           AS DECIMAL(18,2)) AS Price,
            d.Name AS Department
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain  im  ON oh.ItemID = im.ItemID  AND im.Status = 1
        JOIN Store     st  ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ist.ReorderPoint IS NOT NULL AND ist.ReorderPoint > 0
          AND oh.Qty >= 0 AND oh.Qty <= ist.ReorderPoint
        ORDER BY (oh.Qty - ist.ReorderPoint) ASC, st.Sort
    """)

    # Negative stock
    print("  Negative stock items...")
    negative_stock = q(cur, f"""
        {ONHAND_CTE}
        SELECT TOP 100
            im.Name, im.BarcodeNumber,
            st.StoreName,
            CAST(oh.Qty      AS DECIMAL(18,1)) AS OnHand,
            CAST(ist.Price   AS DECIMAL(18,2)) AS Price,
            d.Name AS Department
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain  im  ON oh.ItemID = im.ItemID  AND im.Status = 1
        JOIN Store     st  ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE oh.Qty < 0
        ORDER BY oh.Qty ASC
    """)

    # By department
    print("  Department breakdown...")
    by_department = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            ISNULL(d.Name, N'ללא מחלקה') AS Department,
            st.StoreName,
            SUM(CASE WHEN oh.Qty > 0 THEN 1 ELSE 0 END)                       AS InStock,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty ELSE 0 END) AS DECIMAL(18,1)) AS TotalUnits,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty * ISNULL(ist.AVGCost, 0) ELSE 0 END)
                 AS DECIMAL(18,0))                                             AS Value
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain  im  ON oh.ItemID = im.ItemID  AND im.Status = 1
        JOIN Store     st  ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE oh.Qty > 0
        GROUP BY d.Name, st.StoreName, st.Sort
        HAVING SUM(oh.Qty) > 0
        ORDER BY SUM(oh.Qty * ISNULL(ist.AVGCost, 0)) DESC
    """)

    conn.close()

    data = {
        "last_updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "store_summary": store_summary,
        "low_stock": low_stock,
        "negative_stock": negative_stock,
        "by_department": by_department,
    }

    os.makedirs("docs", exist_ok=True)
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=serial, indent=2)

    print(f"Done. Saved docs/data.json")
    for s in store_summary:
        print(f"  {s['StoreName']}: {s['InStock']} in stock / {s['TotalUnits']} units / ₪{int(s['StockValue']):,}")

if __name__ == "__main__":
    main()
