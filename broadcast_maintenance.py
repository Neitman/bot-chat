"""CLI script to broadcast system maintenance notice to all users."""

import argparse
import logging
import sys

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from services.broadcast_service import BroadcastService

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Broadcast maintenance notice to Telegram bot users.")
    parser.add_argument(
        "duration",
        nargs="?",
        type=int,
        default=30,
        help="Maintenance duration in minutes (default: 30)",
    )
    args = parser.parse_args()

    print(f"🚀 Bắt đầu gửi thông báo bảo trì hệ thống ({args.duration} phút) đến toàn bộ khách hàng...")
    result = BroadcastService.broadcast_maintenance_sync(duration_minutes=args.duration)
    print("\n✅ Hoàn tất broadcast bảo trì:")
    print(f"• Tổng số khách hàng: {result.get('total')}")
    print(f"• Gửi thành công: {result.get('sent')}")
    print(f"• Thất bại / Block bot: {result.get('failed')} (Bị chặn: {result.get('blocked')})")
    if "error" in result:
        print(f"• Lỗi: {result.get('error')}")
