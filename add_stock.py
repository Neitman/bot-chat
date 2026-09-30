"""Digital Account Inventory Management CLI Tool.

Enables administrators to view stock, import accounts individually or in bulk,
from files, interactive prompts, or command-line arguments.
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database.database import get_db
from database.models import Product
from services.account_service import AccountService
from services.broadcast_service import BroadcastService


def show_stock() -> None:
    """Print current stock levels across all products."""
    with get_db() as db:
        summary = AccountService.get_stock_summary(db)
        print("\n" + "=" * 65)
        print("📦 BÁO CÁO TỒN KHO TÀI KHOẢN (DIGITAL INVENTORY)")
        print("=" * 65)
        print(f"{'ID':<4} | {'Tên sản phẩm':<35} | {'Giá (VND)':<10} | {'Tồn kho':<8} | {'Đã bán':<6}")
        print("-" * 65)
        for item in summary:
            price_str = f"{item['price']:,}đ"
            print(
                f"{item['product_id']:<4} | "
                f"{item['product_name'][:35]:<35} | "
                f"{price_str:<10} | "
                f"{item['available_stock']:<8} | "
                f"{item['sold_count']:<6}"
            )
        print("=" * 65 + "\n")


def import_accounts(product_id: int, content: str, note: str = "") -> None:
    """Import accounts from raw string content."""
    with get_db() as db:
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            print(f"❌ Lỗi: Không tìm thấy sản phẩm với ID #{product_id}!")
            return

        result = AccountService.add_accounts_bulk(
            db=db,
            product_id=product_id,
            text_content=content,
            note=note,
        )

        print("\n" + "━" * 50)
        print(f"✅ KẾT QUẢ NẠP TÀI KHOẢN CHO: {product.name}")
        print("━" * 50)
        print(f"• Tổng số dòng đọc được: {result['total_lines']}")
        print(f"• Thêm mới thành công:   {result['added']} tài khoản")
        print(f"• Bỏ qua do trùng lặp:   {result['duplicates']}")
        if result["errors"]:
            print(f"• Lỗi định dạng ({len(result['errors'])} dòng):")
            for err in result["errors"][:5]:
                print(f"   - {err}")
        print(f"• Số lượng tồn kho hiện tại: {result['new_stock']} tài khoản")
        print("━" * 50 + "\n")

        # Automatically broadcast restock notification to all registered customers
        if result["added"] > 0:
            print("📢 Đang gửi thông báo hàng mới về đến tất cả khách hàng...")
            b_res = BroadcastService.broadcast_restock_sync(product_id, result["added"])
            print(f"✅ Đã phát thông báo: {b_res.get('sent', 0)}/{b_res.get('total', 0)} khách hàng nhận thành công.\n")


def interactive_mode() -> None:
    """Run interactive CLI wizard."""
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║     QUẢN LÝ KHO HÀNG TÀI KHOẢN CHATGPT PLUS             ║")
    print("╚" + "═" * 58 + "╝")

    show_stock()

    with get_db() as db:
        products = db.query(Product).order_by(Product.id.asc()).all()
        if not products:
            print("Chưa có sản phẩm nào trong hệ thống.")
            return

    while True:
        try:
            prod_id_input = input("👉 Nhập ID sản phẩm muốn nạp hàng (hoặc gõ 'q' để thoát): ").strip()
            if prod_id_input.lower() in ("q", "quit", "exit"):
                break

            product_id = int(prod_id_input)
            with get_db() as db:
                product = db.query(Product).filter(Product.id == product_id).first()
                if not product:
                    print(f"❌ Không tìm thấy sản phẩm #{product_id}. Vui lòng thử lại.")
                    continue

            print(f"\nĐang chọn: [{product.id}] {product.name}")
            print("Định dạng hỗ trợ:")
            print("  email | pass | 2fa_secret (hoặc email|pass)")
            print("Ví dụ: biradarguru37@googlemail.com | CHATLGBT9999 | E6M7ATQ7QHEALOH7BU2RN6YZRQNBMBE6")
            print("\n👉 Hãy DÁN danh sách tài khoản vào bên dưới (Nhấn Enter 2 lần hoặc gõ 'END' để kết thúc):")

            lines = []
            while True:
                line = input()
                if line.strip() == "END":
                    break
                if not line.strip() and lines and not lines[-1].strip():
                    break
                lines.append(line)

            text_content = "\n".join(lines).strip()
            if not text_content:
                print("⚠️ Chưa có nội dung nào được dán. Bỏ qua.")
                continue

            import_accounts(product_id=product_id, content=text_content)
            show_stock()

        except ValueError:
            print("⚠️ Vui lòng nhập số ID hợp lệ.")
        except (KeyboardInterrupt, EOFError):
            print("\nĐã hủy.")
            break


def main() -> None:
    """Command line parser."""
    parser = argparse.ArgumentParser(description="Tool nhập và quản lý kho tài khoản ChatGPT Plus")
    parser.add_argument("--list", "-l", action="store_true", help="Xem danh sách tồn kho")
    parser.add_argument("--product", "-p", type=int, help="ID của sản phẩm cần nạp")
    parser.add_argument("--text", "-t", type=str, help="Chuỗi tài khoản cần nạp (email|pass|2fa)")
    parser.add_argument("--file", "-f", type=str, help="Đường dẫn file .txt chứa danh sách tài khoản")
    parser.add_argument("--note", type=str, default="", help="Ghi chú đính kèm khi nhập")

    args = parser.parse_args()

    if args.list:
        show_stock()
        return

    if args.product and args.text:
        import_accounts(product_id=args.product, content=args.text, note=args.note)
        return

    if args.product and args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"❌ Không tìm thấy file: {file_path}")
            return
        content = file_path.read_text(encoding="utf-8")
        import_accounts(product_id=args.product, content=content, note=args.note)
        return

    # If no specific arguments provided, run interactive mode
    interactive_mode()


if __name__ == "__main__":
    main()
