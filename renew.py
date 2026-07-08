#!/usr/bin/env python3
"""HostMyBot auto-renewal script.
Checks balance and renews server if enough credits (50).
If balance is close, waits and retries until enough.
Sends result to Telegram.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

PANEL_URL = "https://client.hostmybot.net"
SERVER_ID = os.environ.get('SERVER_ID', '')  
RENEW_COST = 50
MAX_WAIT_MINUTES = 120       # 最多等 120 分钟
CHECK_INTERVAL_SEC = 120     # 每 2 分钟检查一次

API_TOKEN = os.environ.get("HOSTMYBOT_TOKEN", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")


def api_get(path: str) -> dict:
    req = urllib.request.Request(
        f"{PANEL_URL}{path}",
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def api_post(path: str) -> dict:
    req = urllib.request.Request(
        f"{PANEL_URL}{path}",
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        data=b"{}",
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def send_tg(msg: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("[WARN] Telegram not configured, skipping notification")
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            data=data,
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        if result.get("ok"):
            print("[OK] Telegram notification sent")
        else:
            print(f"[WARN] Telegram API returned: {result}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[WARN] Telegram HTTP {e.code}: {body}")
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}")


def check_balance() -> dict:
    """Check renewal status and return status dict."""
    return api_get(f"/api/client/servers/{SERVER_ID}/renewal")


def try_renew(days_to_add: int) -> tuple[bool, str, int]:
    """Try to renew. Returns (success, message, new_balance)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        result = api_post(f"/api/client/servers/{SERVER_ID}/renewal")
        if "error" in result:
            return False, f"错误: {result['error']}", 0
        new_balance = result.get("balance", 0)
        return True, (
            f"✅ <b>HostMyBot 续期成功!</b>\n"
            f"⏰ {now}\n"
            f"📅 +{days_to_add} 天\n"
            f"💰 余额: {new_balance} credits"
        ), new_balance
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            err = json.loads(body)
            detail = err.get("error", body)
        except Exception:
            detail = body
        return False, f"HTTP {e.code}: {detail}", 0
    except Exception as e:
        return False, f"错误: {e}", 0


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Step 1: Check renewal status
    try:
        status = check_balance()
    except Exception as e:
        msg = f"❌ <b>HostMyBot 续期失败</b>\n⏰ {now}\n查询状态出错: {e}"
        print(msg)
        send_tg(msg)
        sys.exit(1)

    balance = status.get("balance", 0)
    can_renew = status.get("can_renew", False)
    suspended = status.get("suspended", False)
    renewable = status.get("renewable", False)
    days_to_add = status.get("days_to_add", 7)

    print(f"Balance: {balance} credits")
    print(f"Can renew: {can_renew}")
    print(f"Suspended: {suspended}")
    print(f"Cost: {RENEW_COST} credits → +{days_to_add} days")

    # Step 2: Handle suspended / not renewable
    if suspended:
        msg = (
            f"⚠️ <b>HostMyBot 服务器已暂停</b>\n"
            f"⏰ {now}\n"
            f"💰 余额: {balance} credits\n"
            f"需要手动处理"
        )
        print(msg)
        send_tg(msg)
        sys.exit(1)

    if not renewable:
        msg = (
            f"ℹ️ <b>HostMyBot 服务器不可续期</b>\n"
            f"⏰ {now}\n"
            f"💰 余额: {balance} credits"
        )
        print(msg)
        send_tg(msg)
        return

    # Step 3: Enough balance → renew immediately
    if balance >= RENEW_COST:
        success, msg, new_balance = try_renew(days_to_add)
        print(msg)
        send_tg(msg)
        return

    # Step 4: Not enough → wait and retry
    deficit = RENEW_COST - balance
    if deficit > MAX_WAIT_MINUTES:
        msg = (
            f"⏳ <b>HostMyBot 余额不足</b>\n"
            f"⏰ {now}\n"
            f"💰 余额: {balance}/{RENEW_COST} credits\n"
            f"还差 {deficit} credits（约 {deficit} 分钟）\n"
            f"超出最大等待时间，需要手动刷 credits"
        )
        print(msg)
        send_tg(msg)
        return

    # Close enough → notify and start waiting
    msg = (
        f"⏳ <b>HostMyBot 余额不足，等待中...</b>\n"
        f"⏰ {now}\n"
        f"💰 余额: {balance}/{RENEW_COST} credits\n"
        f"还差 {deficit} credits\n"
        f"每 {CHECK_INTERVAL_SEC} 秒检查一次，够了自动续期"
    )
    print(msg)
    send_tg(msg)

    elapsed = 0
    while elapsed < MAX_WAIT_MINUTES * 60:
        time.sleep(CHECK_INTERVAL_SEC)
        elapsed += CHECK_INTERVAL_SEC
        print(f"[WAIT] 已等待 {elapsed // 60} 分钟，检查余额...")

        try:
            status = check_balance()
            balance = status.get("balance", 0)
            renewable = status.get("renewable", False)
        except Exception as e:
            print(f"[WARN] 查询失败: {e}，继续等待...")
            continue

        if not renewable:
            print("[INFO] 服务器已不可续期，退出")
            return

        if balance >= RENEW_COST:
            print(f"[INFO] 余额够了！{balance} credits，开始续期...")
            success, msg, new_balance = try_renew(status.get("days_to_add", 7))
            print(msg)
            send_tg(msg)
            return

        print(f"[INFO] 余额 {balance}/{RENEW_COST}，还差 {RENEW_COST - balance}，继续等...")

    # Timeout
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"⏰ <b>HostMyBot 等待超时</b>\n"
        f"等待 {MAX_WAIT_MINUTES} 分钟后余额仍不足\n"
        f"💰 当前余额: {balance}/{RENEW_COST} credits"
    )
    print(msg)
    send_tg(msg)


if __name__ == "__main__":
    import urllib.parse
    main()
