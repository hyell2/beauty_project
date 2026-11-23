"""
지그재그 기획전(이벤트) 상품 수집기

사용법:
    python scrape.py --cat1 뷰티             # 카테고리 페이지 배너에 걸린 이벤트를 찾아 수집 (기본)
    python scrape.py <URL> [<URL> ...]       # 지정한 이벤트만
    python scrape.py --related               # 각 이벤트에 연결된 관련 기획전까지 (1단계)
    python scrape.py --cat1 뷰티             # 뷰티 대분류만 저장 (output/뷰티/)
    python scrape.py --from-raw --cat1 패션잡화  # 다시 수집하지 않고 raw.json에서 다른 카테고리만 뽑기
    옵션: --out 폴더, --site 폴더(사이트 data.js 저장 위치), --headed(브라우저 창 표시)
    (수집한 전체 원본은 항상 output/raw.json 에 보관)

결과:
    output/zigzag_data.xlsx   엑셀 (시트: 이벤트 / 상품 / 이벤트별상품)
    output/events.csv         이벤트 1개당 1행 (기간, 혜택 문구, 쿠폰 %)
    output/products.csv       상품 1개당 1행
    output/event_products.csv 어떤 이벤트·섹션·탭에 몇 번째로 노출됐는지
    ../zigzag-compare/data.js 비교 사이트용 데이터

동작 방식:
    1) Playwright로 이벤트 페이지를 열고 끝까지 스크롤하며 사이트가 호출하는
       GraphQL 응답(api.zigzag.kr/api/2/graphql)을 가로챈다.
    2) 섹션별 상품 ID 목록을 모은 뒤, 사이트가 쓰는 것과 같은 상품 상세 쿼리를
       브라우저 안에서 60개씩 나눠 호출한다 (요청 사이 대기).
    3) 프리셋 섹션은 커서 기반으로 다음 페이지를 끝까지 가져온다.
"""
import argparse
import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

DEFAULT_URL = "https://zigzag.kr/events/eb0be80d-11bf-42f1-9cfd-e85b73e175fb"
# 진행 중인 이벤트 목록은 이 카테고리 페이지의 배너에서 확인
BANNER_PAGE = "https://zigzag.kr/pages/srp-clp-category?category_id=1098"
UUID_RE = re.compile(r"events(?:/|%2F)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
GQL = "https://api.zigzag.kr/api/2/graphql/"
# 기본 headless UA(HeadlessChrome)로는 데이터 API 호출이 일어나지 않으므로 일반 브라우저 UA 사용
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BATCH = 60          # 상세 조회 1회당 상품 수 (사이트 기본값과 동일)
DELAY = 0.7         # 요청 간 대기(초)
HERE = Path(__file__).parent
SHARED = {"ux_query": None}

PRODUCT_FIELDS = ["product_id", "shop_id", "shop_name", "title", "final_price", "price_before_coupon",
                  "coupon_label", "coupon_min", "coupon_max", "coupon_status", "original_price",
                  "discount_rate", "review_score", "review_count", "free_shipping", "is_zonly",
                  "is_brand", "sales_status", "cat1", "cat2", "cat3", "product_url", "image_url"]
EVENT_FIELDS = ["event_id", "title", "description", "start", "end", "max_coupon_pct",
                "max_discount_pct", "free_shipping", "benefits", "product_count", "url", "image"]


def event_id_of(url):
    return url.rstrip("/").split("/events/")[-1].split("?")[0].split("#")[0]


class Collector:
    def __init__(self):
        self.tabs = {}            # (component_key, chip) -> [product_id, ...]
        self.cards = {}           # product_id -> card dict
        # 상세 조회 GraphQL 쿼리 원문. 프리셋 섹션만 있는 기획전에서는 캡처되지 않으므로
        # 앞서 다른 이벤트에서 캡처한 것을 같이 사용
        self.ux_query = SHARED["ux_query"]
        self.presets = {}         # preset uuid -> (query, input)
        self.preset_items = {}    # preset uuid -> [card dict]
        self.related = []         # 관련 기획전 content_list

    def on_response(self, resp):
        if not resp.url.startswith(GQL):
            return
        op = resp.url.rsplit("/", 1)[-1].split("?")[0]
        try:
            req = json.loads(resp.request.post_data or "{}")
            data = resp.json().get("data") or {}
        except Exception:
            return

        if op == "GetCmsContentProductTab":
            key = req["variables"]["input"].get("display_component_key", "?")
            mod = data.get("cms_content_product_tab_module_product_list") or {}
            for chip in mod.get("item_list") or []:
                ids = [p["product_id"] for p in chip.get("item_list") or []]
                self.tabs[(key, chip.get("chip_button") or "")] = ids

        elif op == "GetEventUxGoodsCardList":
            if self.ux_query is None:
                self.ux_query = SHARED["ux_query"] = req["query"]
            for c in data.get("getUxCachedProductCardItemList") or []:
                self.cards[c["catalog_product_id"]] = normalize_ux(c)

        elif op == "GetCmsProductsPresetProductCardItemList":
            inp = req["variables"]["input"]
            uuid = inp["products_preset_uuid"]
            self.presets.setdefault(uuid, (req["query"], inp))
            lst = data.get("cms_products_preset_product_card_item_list") or {}
            if inp.get("after") is None:  # 첫 페이지만 여기서 저장, 나머지는 직접 페이징
                self.preset_items[uuid] = [normalize_preset(i) for i in lst.get("item_list") or []]

        elif op == "GetCmsContentEditorGroup":
            grp = data.get("content_editor_group") or {}
            self.related.extend(grp.get("content_list") or [])


def normalize_ux(c):
    cats = sorted(c.get("managed_category_list") or [], key=lambda x: x.get("depth", 0))
    # depth1 '패션의류' 다음의 '여성 패션의류' 같은 성별 단계는 건너뛰고 실제 분류만 사용
    names = [x.get("value", "") for x in cats if "패션의류" not in x.get("value", "") or x.get("depth") == 1]
    names += ["", "", ""]
    pid = c.get("catalog_product_id")
    return {
        "product_id": pid,
        "shop_id": c.get("shop_id"),
        "shop_name": c.get("shop_name"),
        "title": c.get("title"),
        "final_price": c.get("final_price"),
        "original_price": c.get("max_price"),
        "discount_rate": c.get("discount_rate"),
        "review_score": c.get("review_score"),
        "review_count": int((c.get("display_review_count") or "0").replace(",", "") or 0) or None,
        "free_shipping": c.get("free_shipping"),
        "is_zonly": c.get("is_zonly"),
        "is_brand": c.get("is_brand"),
        "sales_status": c.get("sales_status") or c.get("sellable_status"),
        "cat1": names[0], "cat2": names[1], "cat3": names[2],
        "product_url": f"https://zigzag.kr/catalog/products/{pid}",
        "image_url": c.get("image_url"),
    }


def normalize_preset(item):
    p, s = item.get("product") or {}, item.get("shop") or {}
    pid = p.get("catalog_product_id")
    return {
        "product_id": pid,
        "shop_id": s.get("id") or p.get("shop_id"),
        "shop_name": s.get("name"),
        "title": p.get("title"),
        "final_price": p.get("final_price"),
        "original_price": p.get("price"),
        "discount_rate": p.get("discount_rate"),
        "review_score": p.get("review_score"),
        "review_count": p.get("review_count") or None,
        "free_shipping": p.get("free_shipping"),
        "is_zonly": p.get("is_zonly"),
        "is_brand": p.get("is_brand"),
        "sales_status": p.get("sellable_status"),
        "cat1": "", "cat2": "", "cat3": "",
        "product_url": f"https://zigzag.kr/catalog/products/{pid}",
        "image_url": p.get("image_url"),
    }


def gql(page, op, query, variables):
    """브라우저 컨텍스트 안에서 사이트와 동일하게 GraphQL 호출."""
    return page.evaluate(
        """async ([url, body]) => {
            const r = await fetch(url, {method: 'POST', credentials: 'include',
                headers: {'content-type': 'application/json'}, body: JSON.stringify(body)});
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return await r.json();
        }""",
        [GQL + op, {"query": query, "variables": variables}],
    )


def scroll_to_end(page, max_rounds=80):
    last_h, still = 0, 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(900)
        h = page.evaluate("document.body.scrollHeight")
        still = still + 1 if h == last_h else 0
        if still >= 3:
            break
        last_h = h


def read_event_meta(page, url):
    meta = page.evaluate("""() => {
        const m = n => (document.querySelector(`meta[property="${n}"]`) || {}).content || '';
        const nd = (document.getElementById('__NEXT_DATA__') || {}).textContent || '';
        return {title: m('og:title') || document.title, description: m('og:description'),
                image: m('og:image'), next: nd, body: document.body.innerText.slice(0, 4000)};
    }""")
    ds = re.search(r'"date_started":(\d{13})', meta["next"])
    de = re.search(r'"date_ended":(\d{13})', meta["next"])
    fmt = lambda m: datetime.fromtimestamp(int(m.group(1)) / 1000).strftime("%Y-%m-%d %H:%M") if m else ""
    return {"event_id": event_id_of(url), "url": url, "title": meta["title"],
            "description": meta["description"], "image": meta["image"],
            "start": fmt(ds), "end": fmt(de), "_body": meta["body"]}


def summarize_benefits(texts):
    """혜택 문구에서 쿠폰 %, 할인 %, 무료배송 등 키워드 추출."""
    t = " ".join(x for x in texts if x)
    coupons = [int(x) for x in re.findall(r"(\d{1,2})\s*%\s*쿠폰", t)]
    discounts = [int(x) for x in re.findall(r"(\d{1,2})\s*%\s*할인", t)]
    tags = []
    if coupons:
        tags.append(f"최대 {max(coupons)}% 쿠폰")
    if discounts:
        tags.append(f"최대 {max(discounts)}% 할인")
    for kw in ["무료배송", "무제한 쿠폰", "쿠폰팩", "오늘의 딜", "결제 혜택", "최저가"]:
        if kw in t:
            tags.append(kw)
    return {"max_coupon_pct": max(coupons) if coupons else None,
            "max_discount_pct": max(discounts) if discounts else None,
            "free_shipping": "무료배송" in t,
            "benefits": " · ".join(dict.fromkeys(tags))}


def scrape_event(ctx, url, related_info=None):
    col = Collector()
    page = ctx.new_page()
    page.on("response", col.on_response)
    print(f"\n=== {url}")
    page.goto(url, wait_until="networkidle", timeout=60000)
    ev = read_event_meta(page, url)
    print(f"  [1/4] '{ev['title']}' 로딩, 끝까지 스크롤 중...")
    scroll_to_end(page)
    page.wait_for_timeout(1500)
    print(f"  [2/4] 섹션/탭 {len(col.tabs)}개, 화면에서 받은 상품 {len(col.cards)}개")

    all_ids = list(dict.fromkeys(pid for ids in col.tabs.values() for pid in ids))
    missing = [i for i in all_ids if i not in col.cards]
    print(f"  [3/4] 상세 조회: 전체 {len(all_ids)}개 중 {len(missing)}개 추가 조회")
    if missing and not col.ux_query:
        print("        ! 상세 조회 쿼리를 캡처하지 못해 건너뜀")
        missing = []
    for n in range(0, len(missing), BATCH):
        chunk = missing[n:n + BATCH]
        try:
            res = gql(page, "GetEventUxGoodsCardList", col.ux_query,
                      {"catalog_product_id_list": chunk, "use_caching": False})
            for c in (res.get("data") or {}).get("getUxCachedProductCardItemList") or []:
                col.cards[c["catalog_product_id"]] = normalize_ux(c)
        except Exception as e:
            print(f"        ! 배치 {n // BATCH + 1} 실패: {e}")
        time.sleep(DELAY)

    print(f"  [4/4] 프리셋 섹션 {len(col.presets)}개 페이징")
    for uuid, (query, inp) in col.presets.items():
        items = col.preset_items.setdefault(uuid, [])
        after = str(len(items)) if items else None
        while True:
            try:
                res = gql(page, "GetCmsProductsPresetProductCardItemList", query,
                          {"input": dict(inp, after=after)})
            except Exception as e:
                print(f"        ! {uuid} 실패: {e}")
                break
            lst = (res.get("data") or {}).get("cms_products_preset_product_card_item_list") or {}
            new = [normalize_preset(i) for i in lst.get("item_list") or []]
            if after is not None or not items:
                items.extend(new)
            if not lst.get("has_next_page") or not new:
                break
            after = lst.get("end_cursor")
            time.sleep(DELAY)
        for c in items:
            col.cards.setdefault(c["product_id"], c)
        col.tabs[(f"preset:{uuid[:8]}", "")] = [c["product_id"] for c in items]

    # 프리셋 상품은 응답에 카테고리가 없으므로 상세 쿼리로 다시 조회해 카테고리 채우기
    no_cat = [pid for pid, c in col.cards.items() if not c.get("cat1")]
    if no_cat and col.ux_query:
        print(f"        카테고리 없는 상품 {len(no_cat)}개 재조회")
        for n in range(0, len(no_cat), BATCH):
            try:
                res = gql(page, "GetEventUxGoodsCardList", col.ux_query,
                          {"catalog_product_id_list": no_cat[n:n + BATCH], "use_caching": False})
                for c in (res.get("data") or {}).get("getUxCachedProductCardItemList") or []:
                    col.cards[c["catalog_product_id"]] = normalize_ux(c)
            except Exception as e:
                print(f"        ! 카테고리 재조회 실패: {e}")
            time.sleep(DELAY)
    page.close()

    # 이벤트 정보 정리: 페이지 메타 + (관련 기획전 목록에 있던) 공유 문구 + 페이지 상단 텍스트
    share = (related_info or {}).get("share_info") or {}
    ev.update(summarize_benefits([ev["title"], ev["description"], share.get("description"),
                                  share.get("button_title"), ev.pop("_body")]))
    if related_info and not ev["start"]:
        f = lambda t: datetime.fromtimestamp(int(t) / 1000).strftime("%Y-%m-%d %H:%M")
        ev["start"], ev["end"] = f(related_info["date_started"]), f(related_info["date_ended"])
    ev["product_count"] = len({pid for ids in col.tabs.values() for pid in ids})
    print(f"  -> 상품 {ev['product_count']}개 | {ev['benefits']} | {ev['start']} ~ {ev['end']}")
    return ev, col


def discover_events(ctx):
    """카테고리 페이지 배너에 연결된 이벤트 ID 목록을 찾는다."""
    found = []

    def on(resp):
        if resp.url.startswith(GQL):
            try:
                found.extend(UUID_RE.findall(resp.text()))
            except Exception:
                pass

    page = ctx.new_page()
    page.on("response", on)
    page.goto(BANNER_PAGE, wait_until="networkidle", timeout=60000)
    for _ in range(4):  # 하단 배너까지 로딩
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1000)
    found.extend(UUID_RE.findall(page.content()))
    page.close()
    ids = list(dict.fromkeys(found))
    print(f"배너에서 이벤트 {len(ids)}개 발견")
    return [f"https://zigzag.kr/events/{i}" for i in ids]


def coupon_info(pp, final_price):
    """상품 상세 가격 정보(product_price)에서 쿠폰 정보 정리.
    표시 가격(final_price)은 이미 쿠폰이 적용된 '최종 혜택가'이므로, 쿠폰 전 가격 = 최종가 + 쿠폰 할인액."""
    cps = pp.get("coupon_discount_info_list") or []
    if not cps:
        return {"coupon_label": "", "coupon_amount": 0, "coupon_min": None, "coupon_max": None,
                "price_before_coupon": final_price}
    c = max(cps, key=lambda x: x.get("discount_amount") or 0)
    if c.get("discount_type") == "RATE":
        label = f"{(c.get('discount_rate_bp') or 0) / 100:g}% 쿠폰"
    else:
        label = f"{(c.get('discount_amount_of_amount_coupon') or c.get('discount_amount') or 0):,}원 쿠폰"
    amount = c.get("discount_amount") or 0
    return {"coupon_label": label, "coupon_amount": amount, "coupon_min": c.get("min_required_amount"),
            "coupon_max": c.get("max_discount_amount"), "price_before_coupon": final_price + amount}


def enrich_coupons(products, headed=False):
    """상품마다 상세 정보를 조회해 쿠폰 적용 여부·조건·쿠폰 전 가격을 붙인다 (상품당 요청 1회)."""
    pids = list(products)
    if not pids:
        return
    print(f"\n쿠폰 정보 조회: {len(pids)}개 상품 (약 {len(pids) * DELAY / 60:.0f}분)")
    captured = {}

    def on(resp):
        if "GetCatalogProductDetailPageOption" in resp.url and "q" not in captured:
            try:
                req = json.loads(resp.request.post_data or "{}")
                captured["q"] = (req[0] if isinstance(req, list) else req)["query"]
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(locale="ko-KR", user_agent=UA)
        page = ctx.new_page()
        page.on("response", on)
        page.goto(f"https://zigzag.kr/catalog/products/{pids[0]}", wait_until="networkidle", timeout=60000)
        if "q" not in captured:
            print("  ! 상품 상세 쿼리를 캡처하지 못해 쿠폰 조회를 건너뜀")
            browser.close()
            return
        ok = fail = 0
        for n, pid in enumerate(pids, 1):
            prod = products[pid]
            try:
                res = gql(page, "GetCatalogProductDetailPageOption", captured["q"],
                          {"catalog_product_id": pid, "input": {"catalog_product_id": pid}})
                cp = res["data"]["pdp_option_info"]["catalog_product"]
                pp = cp.get("product_price") or {}
                final = (pp.get("final_discount_info") or {}).get("discount_price") or int(prod.get("final_price") or 0)
                prod.update(coupon_info(pp, int(final)))
                prod["coupon_status"] = cp.get("coupon_available_status") or ""
                ok += 1
            except Exception as e:
                fail += 1
                if fail <= 3:
                    print(f"  ! {pid} 실패: {e}")
            if n % 100 == 0:
                print(f"  {n}/{len(pids)}")
            time.sleep(DELAY)
        browser.close()
    print(f"  완료 {ok}개, 실패 {fail}개, 쿠폰 적용 상품 {sum(1 for x in products.values() if x.get('coupon_amount'))}개")


def is_ended(e, now=None):
    now = now or datetime.now().strftime("%Y-%m-%d %H:%M")
    return bool(e.get("end")) and e["end"] < now


def write_outputs(events, products, links, out, site_dir, price_stats=None):
    out.mkdir(parents=True, exist_ok=True)

    def to_csv(name, fields, rows):
        with open(out / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    link_fields = ["event_id", "event_title", "section", "tab", "rank", "product_id", "shop_name", "title"]
    to_csv("events.csv", EVENT_FIELDS, events)
    to_csv("products.csv", PRODUCT_FIELDS, products.values())
    to_csv("event_products.csv", link_fields, links)

    # 엑셀
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    sheets = [
        ("이벤트", EVENT_FIELDS, events,
         ["이벤트ID", "이벤트명", "설명", "시작", "종료", "최대쿠폰%", "최대할인%", "무료배송", "혜택요약", "상품수", "링크", "이미지"]),
        ("상품", PRODUCT_FIELDS, list(products.values()),
         ["상품ID", "쇼핑몰ID", "쇼핑몰", "상품명", "판매가(쿠폰적용)", "쿠폰 전 가격", "쿠폰", "쿠폰 최소구매",
          "쿠폰 최대할인", "쿠폰상태", "정가", "할인율", "평점", "리뷰수", "무료배송",
          "직진배송", "브랜드", "판매상태", "대분류", "중분류", "소분류", "링크", "이미지"]),
        ("이벤트별상품", link_fields, links,
         ["이벤트ID", "이벤트명", "섹션", "탭", "순위", "상품ID", "쇼핑몰", "상품명"]),
    ]
    num_cols = {"final_price", "original_price", "price_before_coupon", "coupon_min", "coupon_max", "discount_rate", "review_score", "review_count",
                "rank", "product_count", "max_coupon_pct", "max_discount_pct"}
    for i, (name, fields, rows, headers) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = name
        ws.append(headers)
        for r in rows:
            vals = []
            for f in fields:
                v = r.get(f)
                if f in num_cols and v not in (None, ""):
                    try:
                        v = float(v) if f == "review_score" else int(v)
                    except (TypeError, ValueError):
                        pass
                vals.append(v)
            ws.append(vals)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="E4337A")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for j, f in enumerate(fields, 1):
            width = {"title": 60, "event_title": 28, "description": 30, "benefits": 40,
                     "product_url": 20, "url": 20, "image_url": 14, "image": 14}.get(f, 12)
            ws.column_dimensions[get_column_letter(j)].width = width
            if f in ("final_price", "original_price", "price_before_coupon", "coupon_min", "coupon_max"):
                for row in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                    row[0].number_format = "#,##0"
    wb.save(out / "zigzag_data.xlsx")

    # 비교 사이트용 데이터
    if site_dir:
        site_dir.mkdir(parents=True, exist_ok=True)
        ev_of = {}
        for l in links:
            ev_of.setdefault(l["product_id"], set()).add(l["event_id"])
        prods = []
        for p in products.values():
            prods.append([p["product_id"], p["shop_name"], p["title"], int(p["final_price"] or 0),
                          int(p["original_price"] or 0), int(p["discount_rate"] or 0),
                          float(p["review_score"] or 0), int(p["review_count"] or 0),
                          bool(p["free_shipping"]), p["cat1"], p["cat2"], p["image_url"],
                          sorted(ev_of.get(p["product_id"], []))] + price_cols(price_stats, p["product_id"])
                         + [p.get("price_before_coupon"), p.get("coupon_label", ""), p.get("coupon_min"),
                            p.get("coupon_status", "")])
        data = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "events": events,
                "historySince": min((s["first"] for s in (price_stats or {}).values()), default=""),
                "fields": ["id", "shop", "title", "price", "orig", "disc", "score", "reviews",
                           "freeShip", "cat1", "cat2", "img", "events",
                           "minPrice", "minDate", "maxPrice", "days",
                           "preCoupon", "couponLabel", "couponMin", "couponStatus"],
                "products": prods}
        (site_dir / "data.js").write_text(
            "window.ZZ_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
            encoding="utf-8")


def price_cols(price_stats, pid):
    s = (price_stats or {}).get(str(pid))
    return [s["min"], s["minDate"], s["max"], s["days"]] if s else [None, None, None, 0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="수집할 이벤트 URL (비우면 카테고리 페이지 배너에서 자동으로 찾음)")
    ap.add_argument("--related", action="store_true", help="각 이벤트에 연결된 관련 기획전도 수집 (1단계)")
    ap.add_argument("--out", default=str(HERE / "output"))
    ap.add_argument("--site", default=str(HERE.parent / "zigzag-compare"), help="사이트 data.js 저장 폴더 ('' 이면 생략)")
    ap.add_argument("--headed", action="store_true", help="브라우저 창을 띄워서 실행")
    ap.add_argument("--cat1", default="", help="이 대분류 상품만 저장 (예: 뷰티). 비우면 전체")
    ap.add_argument("--enrich", action="store_true", help="--from-raw 와 함께: 카테고리 빈 상품을 다시 조회해 raw.json 보완")
    ap.add_argument("--no-coupon", action="store_true", help="상품별 쿠폰 정보 조회를 건너뜀 (빠름)")
    ap.add_argument("--from-raw", action="store_true", help="다시 수집하지 않고 output/raw.json으로 결과만 재생성")
    args = ap.parse_args()
    out, site = Path(args.out), (Path(args.site) if args.site else None)
    raw_path = out / "raw.json"

    if args.from_raw:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        events, products, links = raw["events"], raw["products"], raw["links"]
        if args.enrich:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=not args.headed)
                ctx = browser.new_context(locale="ko-KR", user_agent=UA)
                enrich_categories(ctx, products)
                browser.close()
            raw_path.write_text(json.dumps({"events": events, "products": products, "links": links},
                                           ensure_ascii=False), encoding="utf-8")
    else:
        events, products, links = collect(args)
        out.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps({"events": events, "products": products, "links": links},
                                       ensure_ascii=False), encoding="utf-8")

    if args.cat1:
        products = {k: v for k, v in products.items() if v.get("cat1") == args.cat1}
        links = [l for l in links if l["product_id"] in products]
        events = [dict(e) for e in events]
        for e in events:
            e["product_count"] = len({l["product_id"] for l in links if l["event_id"] == e["event_id"]})
        events = [e for e in events if e["product_count"] > 0]
        out = out / args.cat1

    # 상품별 쿠폰 정보 (끝난 이벤트에만 있는 상품은 제외해 요청 수 줄임)
    if not args.no_coupon:
        live_ids = {e["event_id"] for e in events if not is_ended(e)}
        targets = {l["product_id"] for l in links if l["event_id"] in live_ids}
        enrich_coupons({k: v for k, v in products.items() if k in targets}, args.headed)

    # 가격·이벤트 기록 (끝난 이벤트도 기록에는 남김)
    import history
    history.record("zigzag", events, products.values())
    stats = history.stats("zigzag")

    # 사이트·스냅샷에는 진행 중인 이벤트만
    ended = [e for e in events if is_ended(e)]
    if ended:
        print(f"종료된 이벤트 {len(ended)}개는 사이트에서 숨김 (기록에는 보관): " + ", ".join(e["title"] for e in ended))
        ended_ids = {e["event_id"] for e in ended}
        events = [e for e in events if e["event_id"] not in ended_ids]
        links = [l for l in links if l["event_id"] not in ended_ids]
        live_pids = {l["product_id"] for l in links}
        products = {k: v for k, v in products.items() if k in live_pids}

    write_outputs(events, products, links, out, site, stats)
    print(f"  기록  -> {history.export_excel()}")
    print(f"\n완료: 이벤트 {len(events)}개, 상품 {len(products)}개" + (f" (대분류: {args.cat1})" if args.cat1 else ""))
    print(f"  엑셀  -> {out / 'zigzag_data.xlsx'}")
    print(f"  CSV   -> {out}")
    if site:
        print(f"  사이트 -> {site / 'data.js'}")


def collect(args):
    events, products, links = [], {}, []
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(locale="ko-KR", user_agent=UA, viewport={"width": 1280, "height": 900})
        urls = args.urls or discover_events(ctx)
        queue = [(u, None) for u in urls]
        while queue:
            url, rel = queue.pop(0)
            eid = event_id_of(url)
            if eid in seen:
                continue
            seen.add(eid)
            try:
                ev, col = scrape_event(ctx, url, rel)
            except Exception as e:
                print(f"  ! 실패: {e}")
                continue
            events.append(ev)
            products.update(col.cards)
            for (sec, chip), ids in col.tabs.items():
                for rank, pid in enumerate(ids, 1):
                    c = col.cards.get(pid, {})
                    links.append({"event_id": eid, "event_title": ev["title"], "section": sec, "tab": chip,
                                  "rank": rank, "product_id": pid, "shop_name": c.get("shop_name", ""),
                                  "title": c.get("title", "")})
            # 관련 기획전은 처음 지정한 이벤트에 직접 연결된 것까지만 (꼬리 물기로 끝없이 늘어나는 것 방지)
            if args.related and rel is None:
                for r in col.related:
                    if r.get("uuid") and r["uuid"] not in seen:
                        queue.append((f"https://zigzag.kr/events/{r['uuid']}", r))
        enrich_categories(ctx, products)
        browser.close()
    return events, products, links


def enrich_categories(ctx, products):
    """카테고리가 빈 상품을 상품 상세 쿼리로 다시 조회해 채운다."""
    missing = [pid for pid, c in products.items() if not c.get("cat1")]
    if not missing:
        return
    print(f"\n카테고리 보완: {len(missing)}개 상품 재조회")
    page = ctx.new_page()
    if not SHARED["ux_query"]:  # 쿼리 캡처를 위해 메인 이벤트 페이지를 한 번 연다
        col = Collector()
        page.on("response", col.on_response)
        page.goto(DEFAULT_URL, wait_until="networkidle", timeout=60000)
        for _ in range(5):
            if SHARED["ux_query"]:
                break
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(1000)
    if not SHARED["ux_query"]:
        print("  ! 상세 조회 쿼리를 캡처하지 못해 건너뜀")
        page.close()
        return
    done = 0
    for n in range(0, len(missing), BATCH):
        try:
            res = gql(page, "GetEventUxGoodsCardList", SHARED["ux_query"],
                      {"catalog_product_id_list": missing[n:n + BATCH], "use_caching": False})
            for c in (res.get("data") or {}).get("getUxCachedProductCardItemList") or []:
                products[c["catalog_product_id"]] = normalize_ux(c)
                done += 1
        except Exception as e:
            print(f"  ! 배치 {n // BATCH + 1} 실패: {e}")
        time.sleep(DELAY)
    page.close()
    print(f"  {done}개 보완, 남은 빈 카테고리 {sum(1 for c in products.values() if not c.get('cat1'))}개")


if __name__ == "__main__":
    main()
