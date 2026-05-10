import json
import os
import sys
import time
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
import pytz

MOMO_SEARCH_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
DROP_THRESHOLD = 0.60
GMAIL_ADDRESS = "chiougene@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
SNAPSHOT_FILE = "prices_snapshot.json"
TZ = pytz.timezone("Asia/Taipei")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Referer": "https://www.momoshop.com.tw/",
    "Content-Type": "application/json",
    "Origin": "https://www.momoshop.com.tw",
}


def fetch_page(page: int) -> dict:
    body = {
        "host": "ecmobile",
        "flag": "searchEngine",
        "data": {
            "searchValue": "小米",
            "curPage": str(page),
            "serviceCode": "MT01",
            "has3P": "Y",
            "platform": 16,
            "NAM": "N",
            "stockYN": "N",
        },
    }
    for attempt in range(3):
        try:
            r = requests.post(MOMO_SEARCH_API, headers=HEADERS, json=body, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            print(f"第 {page} 頁請求失敗，5 秒後重試：{e}")
            time.sleep(5)


def get_all_products():
    products = []
    page = 1
    max_page = 1

    while page <= max_page:
        data = fetch_page(page)
        rtn = data.get("rtnSearchData", {})
        if page == 1:
            max_page = min(data.get("maxPage", 1), 20)
            print(f"共 {data.get('totalCnt', '?')} 個搜尋結果，{max_page} 頁")

        items = rtn.get("goodsInfoList", [])
        for item in items:
            name = item.get("goodsName", "")
            if "官方旗艦館" not in name:
                continue
            price = item.get("SALE_PRICE")
            if not price:
                continue
            goods_code = item.get("goodsCode", "")
            products.append({
                "item_id": goods_code,
                "name": name,
                "price": float(price),
                "url": f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={goods_code}",
            })

        page += 1
        if page <= max_page:
            time.sleep(1)

    return products


def send_email(subject, body):
    if not GMAIL_APP_PASSWORD:
        print("GMAIL_APP_PASSWORD 未設定，跳過通知")
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = GMAIL_ADDRESS
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())
    except Exception as e:
        print(f"Email 通知失敗：{e}")


def do_snapshot():
    print("開始抓取商品快照（momo 小米官方旗艦館）...")
    products = get_all_products()
    data = {
        "timestamp": datetime.now(TZ).isoformat(),
        "products": {p["item_id"]: p for p in products},
    }
    Path(SNAPSHOT_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"快照完成，共 {len(products)} 個官方旗艦館商品")


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

    current_products = get_all_products()
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
            subject = f"[momo 價格異常] {p['name'][:20]}... 降幅 {drop*100:.0f}%"
            body = (
                f"[momo 價格異常] 小米官方旗艦館\n\n"
                f"商品：{p['name']}\n"
                f"快照價格：NT${base_price:,.0f}\n"
                f"現在價格：NT${current_price:,.0f}\n"
                f"降幅：{drop * 100:.1f}%\n"
                f"連結：{p['url']}\n"
            )
            print(body)
            send_email(subject, body)

    if not found_any:
        print("無異常，監控完成")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "monitor"
    if mode == "snapshot":
        do_snapshot()
    elif mode == "monitor":
        do_monitor()
    else:
        print(f"未知模式：{mode}")
        sys.exit(1)
