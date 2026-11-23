"""
뷰티컬리 메인 기획전 수집기

사용법:
    python kurly.py              # 메인 페이지를 3번 받아 합침 (기획전이 접속마다 무작위로 바뀜)
    python kurly.py --loads 5

규칙:
    kurly.com robots.txt 는 www.kurly.com 페이지 수집을 허용하지만 api.kurly.com 은 금지한다.
    그래서 브라우저나 API 호출 없이, 웹페이지 HTML에 이미 들어 있는 데이터(__NEXT_DATA__)만 읽는다.
    → 메인에 노출된 기획전(접속당 3개 내외)과 기획전별 앞쪽 상품(약 20개)만 얻을 수 있다.

결과:
    output/kurly/kurly_data.xlsx   (시트: 기획전 / 상품)
    output/kurly/kurly_raw.json
    ../zigzag-compare/kurly.js     비교 사이트용 데이터
"""
import argparse
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

URL = "https://www.kurly.com/main/beauty"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HERE = Path(__file__).parent


def fetch_next_data(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", errors="ignore")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("페이지에서 __NEXT_DATA__를 찾지 못함 (페이지 구조 변경 가능성)")
    return json.loads(m.group(1))


def coupon_text(p):
    """상품 스티커에서 쿠폰 문구 추출 (예: '+15%쿠폰', '12%쿠폰+증정')."""
    texts = []
    for s in p.get("stickers_v2") or []:
        for c in (s.get("content") or {}).get("contents") or []:
            if c.get("text"):
                texts.append(c["text"])
    return " / ".join(texts)


def parse(next_data, collected_at):
    collections, products = {}, {}
    queries = next_data["props"]["pageProps"].get("dehydratedState", {}).get("queries", [])
    for q in queries:
        sec = ((q.get("state") or {}).get("data") or {}).get("data") or {}
        if sec.get("template_code") != "random_collection":
            continue
        body = sec.get("data") or {}
        code = body.get("collection_code") or str(sec.get("id"))
        cid = f"kurly_{code}"
        ids = []
        for p in body.get("products") or []:
            pid = f"k{p['no']}"
            ids.append(pid)
            brand = re.match(r"\s*\[([^\]]+)\]", p.get("name") or "")
            ctext = coupon_text(p)
            pct = [int(x) for x in re.findall(r"(\d{1,2})\s*%\s*쿠폰", ctext)]
            rc = str(p.get("review_count") or "")
            products[pid] = {
                "product_id": pid,
                "goods_no": p["no"],
                "brand": brand.group(1) if brand else "",
                "title": p.get("name"),
                "description": p.get("short_description") or "",
                "final_price": p.get("discounted_price") or p.get("sales_price"),
                "original_price": p.get("sales_price"),
                "discount_rate": p.get("discount_rate") or 0,
                "review_count_text": rc,
                "review_count": int(re.sub(r"\D", "", rc) or 0),
                "coupon_text": ctext,
                "coupon_pct": max(pct) if pct else None,
                "kurly_only": any(t.get("type") == "KURLY_ONLY" for t in p.get("tags") or []),
                "sold_out": bool(p.get("is_sold_out")),
                "product_url": f"https://www.kurly.com/goods/{p['no']}",
                "image_url": p.get("list_image_url"),
            }
        end = body.get("end_datetime")
        collections[cid] = {
            "event_id": cid,
            "collection_code": code,
            "title": sec.get("title") or code,
            "description": sec.get("subtitle") or "",
            "start": collected_at,           # 시작일 정보가 없어 수집일을 기준으로 사용
            "end": end[:16].replace("T", " ") if end else "",
            "url": f"https://www.kurly.com/collections/{code}?site=beauty",
            "product_ids": ids,
        }
    return collections, products


def enrich_coupons(products, delay=2.0):
    """상품 상세 페이지(www.kurly.com/goods/…, 수집 허용 경로) HTML에서 쿠폰 정보를 읽는다.
    목록의 할인가는 쿠폰이 있으면 이미 쿠폰 적용가이므로, 쿠폰 전 가격을 따로 구한다."""
    print(f"쿠폰 정보 조회: {len(products)}개 상품 (약 {len(products) * delay / 60:.0f}분)")
    ok = 0
    for n, p in enumerate(products.values(), 1):
        if n > 1:
            time.sleep(delay)
        try:
            prod = fetch_next_data(p["product_url"])["props"]["pageProps"]["product"]
        except Exception as e:
            print(f"  ! {p['goods_no']} 실패: {e}")
            continue
        deals = prod.get("dealProducts") or []
        # 목록 가격과 같은 쿠폰가를 가진 옵션(없으면 첫 옵션)을 기준으로 쿠폰 전 가격 계산
        deal = next((d for d in deals if d.get("couponDiscountedPrice") == p["final_price"]), deals[0] if deals else {})
        avail = bool(prod.get("couponAvailable")) and bool(deal.get("couponDiscountedPrice"))
        p["coupon_available"] = avail
        p["price_before_coupon"] = (deal.get("discountedPrice") if avail else None) or p["final_price"]
        end = prod.get("earliestCouponPublishEndAt")
        p["coupon_end"] = end[:16].replace("T", " ") if avail and end else ""
        if avail and p["price_before_coupon"] > p["final_price"]:
            pct = round((1 - p["final_price"] / p["price_before_coupon"]) * 100)
            p["coupon_label"] = f"{pct}% 쿠폰"
        else:
            p["coupon_label"] = ""
        ok += 1
        if n % 20 == 0:
            print(f"  {n}/{len(products)}")
    print(f"  완료 {ok}개, 쿠폰 적용 상품 {sum(1 for x in products.values() if x.get('coupon_label'))}개")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loads", type=int, default=3, help="메인 페이지를 몇 번 받을지 (기획전이 무작위로 바뀜)")
    ap.add_argument("--no-coupon", action="store_true", help="상품별 쿠폰 정보 조회 건너뜀")
    ap.add_argument("--out", default=str(HERE / "output" / "kurly"))
    ap.add_argument("--site", default=str(HERE.parent / "zigzag-compare"))
    args = ap.parse_args()
    out, site = Path(args.out), Path(args.site)
    out.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    collected_at = now.strftime("%Y-%m-%d")
    collections, products = {}, {}
    for i in range(args.loads):
        if i:
            time.sleep(3)
        try:
            c, p = parse(fetch_next_data(URL), collected_at)
        except Exception as e:
            print(f"  ! {i + 1}번째 실패: {e}")
            continue
        new = [x["title"] for k, x in c.items() if k not in collections]
        collections.update(c)
        products.update(p)
        print(f"  {i + 1}/{args.loads}: 기획전 {len(c)}개 (새로 {len(new)}개: {', '.join(new)})")

    # 안전장치: 한 번도 못 받았으면 기존 결과·사이트 데이터를 빈 값으로 덮어쓰지 않고 종료
    if not collections:
        raise SystemExit("! 기획전을 하나도 받지 못해 저장하지 않고 종료합니다 (기존 데이터 유지)")

    for c in collections.values():
        pcts = [products[i]["coupon_pct"] for i in c["product_ids"] if products[i]["coupon_pct"]]
        c["max_coupon_pct"] = max(pcts) if pcts else None
        c["max_discount_pct"] = max((products[i]["discount_rate"] for i in c["product_ids"]), default=None)
        c["product_count"] = len(c["product_ids"])

    if not args.no_coupon:
        enrich_coupons(products)

    # 가격·기획전 기록 → 상품별 최저가 통계 붙이기
    import history
    history.record("kurly", collections.values(), products.values())
    stats = history.stats("kurly")
    for p in products.values():
        s = stats.get(p["product_id"])
        p.update({"min_price": s["min"], "min_date": s["minDate"], "max_price": s["max"], "days": s["days"]} if s
                 else {"min_price": None, "min_date": None, "max_price": None, "days": 0})
    # 종료일이 지난 기획전은 사이트에서 숨김 (기록에는 보관)
    now_s = now.strftime("%Y-%m-%d %H:%M")
    live = {k: c for k, c in collections.items() if not (c["end"] and c["end"] < now_s)}

    (out / "kurly_raw.json").write_text(json.dumps({"collections": collections, "products": products},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")

    # 엑셀
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "기획전"
    ws.append(["기획전ID", "기획전명", "혜택 문구", "수집일", "종료일", "최대쿠폰%", "최대할인%", "상품수", "링크"])
    for c in collections.values():
        ws.append([c["collection_code"], c["title"], c["description"], c["start"], c["end"] or "정보 없음",
                   c["max_coupon_pct"], c["max_discount_pct"], c["product_count"], c["url"]])
    ws2 = wb.create_sheet("상품")
    ws2.append(["상품번호", "기획전", "브랜드", "상품명", "설명", "판매가(쿠폰적용)", "정가", "할인율", "쿠폰 표시", "쿠폰%",
                "리뷰수", "컬리온리", "품절", "링크", "이미지", "쿠폰 전 가격", "쿠폰", "쿠폰 발급 마감"])
    coll_of = {pid: c["title"] for c in collections.values() for pid in c["product_ids"]}
    for p in products.values():
        ws2.append([p["goods_no"], coll_of.get(p["product_id"], ""), p["brand"], p["title"], p["description"],
                    p["final_price"], p["original_price"], p["discount_rate"], p["coupon_text"], p["coupon_pct"],
                    p["review_count_text"], "Y" if p["kurly_only"] else "", "Y" if p["sold_out"] else "",
                    p["product_url"], p["image_url"], p.get("price_before_coupon"), p.get("coupon_label", ""),
                    p.get("coupon_end", "")])
    for sheet, widths in ((ws, [18, 30, 40, 12, 12, 10, 10, 8, 30]),
                          (ws2, [12, 24, 14, 60, 30, 10, 10, 8, 18, 8, 8, 8, 6, 20, 14])):
        for c in sheet[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="5F0080")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for j, w in enumerate(widths):
            sheet.column_dimensions[chr(65 + j)].width = w
    for row in ws2.iter_rows(min_row=2, min_col=6, max_col=7):
        for cell in row:
            cell.number_format = "#,##0"
    wb.save(out / "kurly_data.xlsx")

    # 사이트용
    site.mkdir(parents=True, exist_ok=True)
    live_pids = {pid for c in live.values() for pid in c["product_ids"]}
    data = {"updated": now.strftime("%Y-%m-%d %H:%M"),
            "historySince": min((s["first"] for s in stats.values()), default=""),
            "collections": list(live.values()),
            "products": [p for p in products.values() if p["product_id"] in live_pids]}
    (site / "kurly.js").write_text("window.KURLY_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
                                   encoding="utf-8")
    print(f"\n완료: 기획전 {len(collections)}개, 상품 {len(products)}개")
    print(f"  엑셀  -> {out / 'kurly_data.xlsx'}")
    print(f"  사이트 -> {site / 'kurly.js'}")
    print(f"  기록  -> {history.export_excel()}")
    if site:
        print(f"  그래프 -> {history.export_site_js(site)}")


if __name__ == "__main__":
    main()
