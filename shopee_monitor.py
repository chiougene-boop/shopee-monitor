import json
import os
import sys
import time
import requests
from datetime import datetime
from pathlib import Path
import pytz

SHOPEE_BASE = "https://shopee.tw/api/v4"
SHOP_USERNAME = "xiaomi.tw"
DROP_THRESHOLD = 0.60
LINE_TOKEN = os.environ.get("LINE_NOTIFY_TOKEN", "")
SNAPSHOT_FILE = "prices_snapshot.json"
TZ = pytz.timezone("Asia/Taipei")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": f"https://shopee.tw/{SHOP_USERNAME}",
    "Accept": "application/json",
    "X-API-SOURCE": "pc",
    "X-Shopee-Language": "zh-Hant",
})


def safe_get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"請求失敗，{5} 秒後重試（{attempt + 1}/{retries}）：{e}")
            time.sleep(5)


def get_shop_id():
    r = safe_get(f"{SHOPEE_BASE}/shop/get_shop_detail", params={"shop_username": SHOP_USERNAME})
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"取得商店資料失敗：{data}")
    return data["data"]["shopid"]


def get_all_products(shop_id):
    products = []
    offset = 0
    limit = 60
    while True:
        r = safe_get(f"{SHOPEE_BASE}/search/search_items", params={
            "by": "pop", "match_id": shop_id, "newest": offset,
            "order": "desc", "page_type": "shop", "scenario": "PAGE_OTHERS",
            "version": 2, "limit": limit,
        })
        data = r.json()
        items = data.get("items") or []
        if not items:
            break
        for item in items:
            info = item.get("item_basic") or item
            raw_price = info.get("price_min") or info.get("price") or 0
            if raw_price <= 0:
                continue
            products.append({
                "item_id": str(info["itemid"]),
                "name": info["name"],
                "price": raw_price / 100000,
                "url": f"https://shopee.tw/product/{info['shopid']}/{info['itemid']}",
            })
        offset += limit
        if len(items) < limit:
            break
        time.sleep(1.5)
    return products


def send_line(message):
    if not LINE_TOKEN:
        print("LINE_NOTIFY_TOKEN 未設定，跳過通知")
        return
    try:
        r = requests.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
            data={"message": message},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"LINE 通知失敗：{e}")


def do_snapshot():
    print("開始抓取商品快照...")
    shop_id = get_shop_id()
    print(f"小米商店 ID：{shop_id}")
    products = get_all_products(shop_id)
    data = {
        "timestamp": datetime.now(TZ).isoformat(),
        "products": {p["item_id"]: p for p in products},
    }
    Path(SNAPSHOT_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"快照完成，共 {len(products)} 個商品")


def do_monitor():
    if not Path(SNAPSHOT_FILE).exists():
        print("找不到快照檔案，跳過監控")
        return

    snapshot_raw = json.loads(Path(SNAPSHOT_FILE).read_text(encoding="utf-8"))
    snapshot = snapshot_raw.get("products", {})
    snapshot_time = snapshot_raw.get("timestamp", "未知")

    if not snapshot:
        print("快照為空，跳過監控（請先手動執行一次 snapshot workflow）")
        return

    print(f"快照時間：{snapshot_time}，共 {len(snapshot)} 個商品")
    print("開始抓取目前價格...")

    shop_id = get_shop_id()
    current_products = get_all_products(shop_id)
    print(f"目前商品數：{len(current_products)}")

    found_any = False
    for p in current_products:
        item_id = p["item_id"]
        if item_id not in snapshot:
            continue
        base_price = snapshot[item_id]["price"]
        current_price = p["price"]
        if base_price <= 0:
            continue
        drop = (base_price - current_price) / base_price
        if drop >= DROP_THRESHOLD:
            found_any = True
            msg = (
                f"\n[蝦皮價格異常] 小米官方店"
                f"\n商品：{p['name']}"
                f"\n快照價格：NT${base_price:,.0f}"
                f"\n現在價格：NT${current_price:,.0f}"
                f"\n降幅：{drop * 100:.1f}%"
                f"\n連結：{p['url']}"
            )
            print(msg)
            send_line(msg)

    if not found_any:
        print("無異常，監控完成")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "monitor"
    if mode == "snapshot":
        do_snapshot()
    elif mode == "monitor":
        do_monitor()
    else:
        print(f"未知模式：{mode}，請使用 snapshot 或 monitor")
        sys.exit(1)
