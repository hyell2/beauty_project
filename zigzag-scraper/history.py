"""
가격·이벤트 기록 저장소 (SQLite: output/history.db)

- 수집할 때마다 상품 가격을 (출처, 상품ID, 날짜) 단위로 쌓는다. 같은 날 여러 번 수집하면 그날 최저가를 유지.
- 이벤트는 끝난 것까지 전부 보관 (first_seen / last_seen).
- export_excel() 로 output/history.xlsx 생성 (이벤트 기록 / 가격 요약 / 가격 기록).

단독 실행:
    python history.py          # history.xlsx 만 다시 만들기
"""
import sqlite3
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "output" / "history.db"
XLSX_PATH = HERE / "output" / "history.xlsx"


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            source TEXT, event_id TEXT, title TEXT, description TEXT, start TEXT, end TEXT,
            max_coupon_pct INTEGER, max_discount_pct INTEGER, benefits TEXT, product_count INTEGER,
            url TEXT, image TEXT, first_seen TEXT, last_seen TEXT,
            PRIMARY KEY (source, event_id));
        CREATE TABLE IF NOT EXISTS prices (
            source TEXT, product_id TEXT, date TEXT, price INTEGER, orig INTEGER, disc INTEGER,
            shop TEXT, title TEXT, category TEXT, url TEXT,
            PRIMARY KEY (source, product_id, date));
        CREATE INDEX IF NOT EXISTS idx_prices_pid ON prices (source, product_id);
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(prices)")}
    if "pre_coupon" not in cols:  # 쿠폰 전 가격 칼럼 (나중에 추가됨)
        con.execute("ALTER TABLE prices ADD COLUMN pre_coupon INTEGER")
        con.execute("ALTER TABLE prices ADD COLUMN coupon TEXT")
    return con


def today():
    return datetime.now().strftime("%Y-%m-%d")


def record(source, events, products, date=None):
    """events: [{event_id, title, ...}], products: [{product_id, final_price, ...}]"""
    date = date or today()
    con = connect()
    with con:
        for e in events:
            con.execute("""
                INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source, event_id) DO UPDATE SET
                    title=excluded.title, description=excluded.description, start=excluded.start,
                    end=excluded.end, max_coupon_pct=excluded.max_coupon_pct,
                    max_discount_pct=excluded.max_discount_pct, benefits=excluded.benefits,
                    product_count=excluded.product_count, url=excluded.url, image=excluded.image,
                    last_seen=excluded.last_seen""",
                (source, e["event_id"], e.get("title"), e.get("description"), e.get("start"), e.get("end"),
                 e.get("max_coupon_pct"), e.get("max_discount_pct"), e.get("benefits"), e.get("product_count"),
                 e.get("url"), e.get("image"), date, date))
        for p in products:
            price = int(p.get("final_price") or 0)
            if price <= 0:
                continue
            con.execute("""
                INSERT INTO prices (source, product_id, date, price, orig, disc, shop, title, category, url,
                                    pre_coupon, coupon)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source, product_id, date) DO UPDATE SET
                    price=MIN(price, excluded.price), orig=excluded.orig, disc=excluded.disc,
                    shop=excluded.shop, title=excluded.title,
                    pre_coupon=COALESCE(excluded.pre_coupon, pre_coupon), coupon=COALESCE(excluded.coupon, coupon)""",
                (source, str(p["product_id"]), date, price, int(p.get("original_price") or 0),
                 int(p.get("discount_rate") or 0), p.get("shop_name") or p.get("brand"), p.get("title"),
                 p.get("cat2") or p.get("category") or "", p.get("product_url"),
                 p.get("price_before_coupon"), p.get("coupon_label") or p.get("coupon_text") or None))
    con.close()


def stats(source):
    """상품별 가격 통계: {product_id: {min, minDate, max, days, first}}"""
    con = connect()
    rows = con.execute("""
        SELECT p.product_id, MIN(p.price), MAX(p.price), COUNT(*), MIN(p.date),
               (SELECT date FROM prices q WHERE q.source=p.source AND q.product_id=p.product_id
                ORDER BY q.price ASC, q.date DESC LIMIT 1)
        FROM prices p WHERE p.source=? GROUP BY p.product_id""", (source,)).fetchall()
    con.close()
    return {pid: {"min": mn, "max": mx, "days": n, "first": first, "minDate": md}
            for pid, mn, mx, n, first, md in rows}


def export_site_js(site_dir, days=365):
    """사이트 그래프용 상품별 가격 기록 → site_dir/history.js
    형식: {d0: 기준일, zigzag: {상품ID: [[기준일부터 일수, 쿠폰적용가, 쿠폰전가격], ...]}, kurly: {...}}"""
    import json
    from datetime import date, timedelta
    con = connect()
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = con.execute("""SELECT source, product_id, date, price, pre_coupon FROM prices
                          WHERE date >= ? ORDER BY source, product_id, date""", (since,)).fetchall()
    con.close()
    d0 = min((r[2] for r in rows), default=date.today().isoformat())
    base = date.fromisoformat(d0)
    out = {"d0": d0, "zigzag": {}, "kurly": {}}
    for src, pid, d, price, pre in rows:
        off = (date.fromisoformat(d) - base).days
        out.setdefault(src, {}).setdefault(pid, []).append([off, price, pre if pre and pre != price else None])
    path = Path(site_dir) / "history.js"
    path.write_text("window.PRICE_HISTORY = " + json.dumps(out, separators=(",", ":")) + ";\n", encoding="utf-8")
    return path


def export_excel(path=XLSX_PATH):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    con = connect()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    wb = Workbook()

    def sheet(ws, headers, rows, widths, money_cols=()):
        ws.append(headers)
        for r in rows:
            ws.append(list(r))
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="444444")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        for j in money_cols:
            for row in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                row[0].number_format = "#,##0"

    ev = con.execute("""SELECT source, title, description, start, end, max_coupon_pct, max_discount_pct,
                        benefits, product_count, first_seen, last_seen, url FROM events
                        ORDER BY COALESCE(NULLIF(end,''), '9999') DESC""").fetchall()
    ws = wb.active
    ws.title = "이벤트 기록"
    sheet(ws, ["출처", "상태", "이벤트명", "설명", "시작", "종료", "최대쿠폰%", "최대할인%", "혜택", "상품수",
               "처음 수집", "마지막 수집", "링크"],
          [(s, "종료" if e and e < now else "진행 중", t, d, st, e or "미정", c, dc, b, n, fs, ls, u)
           for s, t, d, st, e, c, dc, b, n, fs, ls, u in ev],
          [10, 8, 30, 30, 16, 16, 9, 9, 30, 8, 11, 11, 20])

    summ = con.execute("""
        WITH latest AS (
            SELECT source, product_id, MAX(date) AS d FROM prices GROUP BY source, product_id)
        SELECT p.source, p.product_id, cur.shop, cur.title, cur.category, cur.price, MIN(p.price), MAX(p.price),
               (SELECT date FROM prices q WHERE q.source=p.source AND q.product_id=p.product_id
                ORDER BY q.price ASC, q.date DESC LIMIT 1),
               COUNT(*), MIN(p.date), l.d, cur.url
        FROM prices p JOIN latest l USING (source, product_id)
        JOIN prices cur ON cur.source=p.source AND cur.product_id=p.product_id AND cur.date=l.d
        GROUP BY p.source, p.product_id ORDER BY p.source, cur.title""").fetchall()
    ws2 = wb.create_sheet("가격 요약")
    rows = []
    for s, pid, shop, title, cat, cur, mn, mx, mnd, n, first, last, url in summ:
        if n < 2:
            verdict = "기록 쌓는 중"
        elif cur <= mn:
            verdict = "기록상 최저가"
        else:
            verdict = f"최저가보다 {cur - mn:,}원 비쌈"
        rows.append((s, pid, shop, title, cat, cur, mn, mnd, mx, n, first, last, verdict, url))
    sheet(ws2, ["출처", "상품ID", "쇼핑몰/브랜드", "상품명", "카테고리", "최근 가격", "최저가", "최저가 날짜", "최고가",
                "기록 일수", "처음 수집", "마지막 수집", "판정", "링크"],
          rows, [10, 12, 14, 50, 12, 10, 10, 11, 10, 8, 11, 11, 20, 20], money_cols=(6, 7, 9))

    hist = con.execute("""SELECT source, product_id, date, price, pre_coupon, coupon, orig, disc, shop, title
                          FROM prices ORDER BY source, product_id, date""").fetchall()
    ws3 = wb.create_sheet("가격 기록")
    sheet(ws3, ["출처", "상품ID", "날짜", "판매가(쿠폰적용)", "쿠폰 전 가격", "쿠폰", "정가", "할인율",
                "쇼핑몰/브랜드", "상품명"],
          hist, [10, 12, 11, 12, 11, 16, 10, 8, 14, 50], money_cols=(4, 5, 7))
    con.close()
    wb.save(path)
    return path


if __name__ == "__main__":
    print("저장:", export_excel())
