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

def q(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def serial(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%d %H:%M")
    return str(obj)

def main():
    print("Connecting to SQL Server...")
    conn = pyodbc.connect(CONN_STR, timeout=15)
    cur = conn.cursor()

    # Store summary
    store_summary = q(cur, """
        SELECT
            st.StoreName,
            st.Code as StoreCode,
            COUNT(DISTINCT ist.ItemID)                                                          AS TotalSKUs,
            SUM(CASE WHEN ist.OnHand > 0  THEN 1 ELSE 0 END)                                  AS InStock,
            SUM(CASE WHEN ist.OnHand = 0  THEN 1 ELSE 0 END)                                  AS ZeroStock,
            SUM(CASE WHEN ist.OnHand < 0  THEN 1 ELSE 0 END)                                  AS NegativeStock,
            SUM(CASE WHEN ist.ReorderPoint IS NOT NULL
                      AND ist.OnHand >= 0
                      AND ist.OnHand <= ist.ReorderPoint
                      AND ist.ReorderPoint > 0 THEN 1 ELSE 0 END)                             AS LowStock,
            CAST(SUM(ist.OnHand) AS DECIMAL(18,1))                                            AS TotalUnits,
            CAST(SUM(ist.OnHand * ISNULL(ist.AVGCost, 0)) AS DECIMAL(18,0))                  AS StockValue
        FROM Store st
        LEFT JOIN ItemStore ist ON st.StoreID = ist.StoreID
        LEFT JOIN ItemMain im   ON ist.ItemID  = im.ItemID AND im.Status = 1
        WHERE st.Status = 1
        GROUP BY st.StoreID, st.StoreName, st.Code, st.Sort
        ORDER BY st.Sort
    """)

    # Low stock items (ReorderPoint set and OnHand <= ReorderPoint)
    low_stock = q(cur, """
        SELECT TOP 200
            im.Name,
            im.BarcodeNumber,
            im.ModelNumber,
            st.StoreName,
            CAST(ist.OnHand       AS DECIMAL(18,1)) AS OnHand,
            CAST(ist.ReorderPoint AS DECIMAL(18,1)) AS ReorderPoint,
            CAST(ist.Price        AS DECIMAL(18,2)) AS Price,
            d.Name AS Department
        FROM ItemStore ist
        JOIN ItemMain  im ON ist.ItemID  = im.ItemID  AND im.Status = 1
        JOIN Store     st ON ist.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ist.ReorderPoint IS NOT NULL
          AND ist.ReorderPoint > 0
          AND ist.OnHand <= ist.ReorderPoint
          AND ist.OnHand >= 0
        ORDER BY (ist.OnHand - ist.ReorderPoint) ASC, st.Sort
    """)

    # Negative stock items
    negative_stock = q(cur, """
        SELECT TOP 100
            im.Name,
            im.BarcodeNumber,
            st.StoreName,
            CAST(ist.OnHand AS DECIMAL(18,1)) AS OnHand,
            CAST(ist.Price  AS DECIMAL(18,2)) AS Price,
            d.Name AS Department
        FROM ItemStore ist
        JOIN ItemMain  im ON ist.ItemID  = im.ItemID  AND im.Status = 1
        JOIN Store     st ON ist.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ist.OnHand < 0
        ORDER BY ist.OnHand ASC
    """)

    # Department breakdown (items in stock per department)
    by_department = q(cur, """
        SELECT
            ISNULL(d.Name, 'ללא מחלקה') AS Department,
            st.StoreName,
            SUM(CASE WHEN ist.OnHand > 0 THEN 1 ELSE 0 END) AS InStock,
            CAST(SUM(ist.OnHand) AS DECIMAL(18,1))           AS TotalUnits,
            CAST(SUM(ist.OnHand * ISNULL(ist.AVGCost,0)) AS DECIMAL(18,0)) AS Value
        FROM ItemStore ist
        JOIN ItemMain  im ON ist.ItemID  = im.ItemID  AND im.Status = 1
        JOIN Store     st ON ist.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ist.OnHand > 0
        GROUP BY d.Name, st.StoreName, st.Sort
        HAVING SUM(ist.OnHand) > 0
        ORDER BY SUM(ist.OnHand * ISNULL(ist.AVGCost,0)) DESC
    """)

    conn.close()

    data = {
        "last_updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "store_summary": store_summary,
        "low_stock": low_stock,
        "negative_stock": negative_stock,
        "by_department": by_department,
    }

    out = json.dumps(data, ensure_ascii=False, default=serial, indent=2)
    os.makedirs("docs", exist_ok=True)
    with open("docs/data.json", "w", encoding="utf-8") as f:
        f.write(out)

    print(f"Done. Saved docs/data.json")
    print(f"  Stores: {len(store_summary)}")
    print(f"  Low stock items: {len(low_stock)}")
    print(f"  Negative stock: {len(negative_stock)}")
    print(f"  Departments: {len(by_department)}")

if __name__ == "__main__":
    main()
