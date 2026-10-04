"""CLI tool to view and query SQLite database tables directly."""

import sqlite3
import sys
from pathlib import Path

DB_FILE = Path(__file__).parent / "shop_bot.db"


def print_table(cursor, table_name: str, limit: int = 20) -> None:
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [col[1] for col in cursor.fetchall()]
    
    cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
    rows = cursor.fetchall()
    
    print(f"\n{'='*25} BẢNG: {table_name} ({len(rows)} dòng đầu) {'='*25}")
    if not rows:
        print("(Bảng đang trống)")
        return
        
    # Calculate column widths
    col_widths = [len(col) for col in columns]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val) if val is not None else "NULL"))
            
    header = " | ".join(columns[i].ljust(col_widths[i]) for i in range(len(columns)))
    sep = "-+-".join("-" * col_widths[i] for i in range(len(columns)))
    print(header)
    print(sep)
    for row in rows:
        row_str = " | ".join(str(val if val is not None else "NULL").ljust(col_widths[i]) for i, val in enumerate(row))
        print(row_str)


def main():
    if not DB_FILE.exists():
        print(f"Không tìm thấy file database: {DB_FILE}")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg.lower().startswith("select"):
            cursor.execute(arg)
            rows = cursor.fetchall()
            print(f"\nKết quả truy vấn ({len(rows)} dòng):")
            for r in rows:
                print(r)
            conn.close()
            return
        else:
            print_table(cursor, arg)
            conn.close()
            return

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [row[0] for row in cursor.fetchall()]

    print(f"📁 Database: {DB_FILE.name}")
    print(f"📊 Các bảng hiện có: {', '.join(tables)}")

    for t in tables:
        print_table(cursor, t, limit=10)

    conn.close()
    print("\n💡 Gợi ý:")
    print("  • Xem 1 bảng cụ thể: python view_db.py products")
    print("  • Chạy câu query:     python view_db.py \"SELECT id, name, price FROM products\"")


if __name__ == "__main__":
    main()
