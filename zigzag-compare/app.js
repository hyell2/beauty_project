(() => {
  const D = window.ZZ_DATA;
  if (!D) {
    document.querySelector("main").innerHTML =
      '<p style="padding:40px 0">data.js가 없어요. zigzag-scraper에서 <code>python scrape.py --cat1 뷰티</code>를 먼저 실행하세요.</p>';
    return;
  }

  const $ = (s) => document.querySelector(s);
  const won = (n) => n.toLocaleString("ko-KR") + "원";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
  const COLORS = ["#e0356f", "#7048e8", "#1c7ed6", "#0ca678", "#f08c00", "#ae3ec9", "#495057"];
  const PLATFORM_COLORS = { "에이블리": "#ff4d6d", "무신사": "#222222", "올리브영": "#9bc31f", "29CM": "#111111", "W컨셉": "#000000" };
  const STORE_KEY = "dealradar.manualEvents.v1";

  // ---- 상품 ----
  const F = D.fields;
  const products = D.products.map((row) => {
    const p = Object.fromEntries(F.map((f, i) => [f, row[i]]));
    return { ...p, source: "zigzag", url: `https://zigzag.kr/catalog/products/${p.id}`, reviewsText: p.reviews ? p.reviews.toLocaleString() : "" };
  });
  // 뷰티컬리 (kurly.js 가 있을 때만)
  const K = window.KURLY_DATA;
  if (K) {
    const collOf = {};
    K.collections.forEach((c) => c.product_ids.forEach((id) => (collOf[id] = collOf[id] || []).push(c.event_id)));
    K.products.forEach((p) => products.push({
      id: p.product_id, source: "kurly", url: p.product_url, shop: p.brand || "뷰티컬리", title: p.title,
      price: p.final_price || 0, orig: p.original_price || 0, disc: p.discount_rate || 0, score: 0,
      reviews: p.review_count || 0, reviewsText: p.review_count_text, freeShip: false, cat1: "뷰티", cat2: "",
      img: p.image_url, events: collOf[p.product_id] || [], coupon: p.coupon_text, soldOut: p.sold_out,
      minPrice: p.min_price, minDate: p.min_date, maxPrice: p.max_price, days: p.days || 0,
      preCoupon: p.price_before_coupon, couponLabel: p.coupon_label || "", couponEnd: p.coupon_end || "",
    }));
  }
  const now = new Date();
  const parseDate = (s, endOfDay) => {
    if (!s) return null;
    const d = new Date(s.length <= 10 ? s + (endOfDay ? "T23:59:59" : "T00:00:00") : s.replace(" ", "T"));
    return isNaN(d) ? null : d;
  };

  // ---- 직접 입력 이벤트 (브라우저에 저장) ----
  function loadManual() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); } catch { return []; }
  }
  function saveManual(list) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); } catch { alert("브라우저 저장소에 저장하지 못했어요. 내보내기로 백업해 두세요."); }
  }
  let manual = loadManual();

  // ---- 이벤트 통합 ----
  let events = [], evById = {}, hiddenEnded = [], showEnded = false;
  function buildEvents() {
    const auto = D.events.map((e, i) => {
      const mine = products.filter((p) => p.events.includes(e.event_id));
      const discs = mine.map((p) => p.disc);
      return {
        ...e, kind: "auto", platform: "지그재그", color: COLORS[i % COLORS.length],
        startDate: parseDate(e.start), endDate: parseDate(e.end, true),
        avgDisc: discs.length ? Math.round(discs.reduce((a, b) => a + b, 0) / discs.length) : null,
        over50: discs.filter((d) => d >= 50).length,
        cheapest: mine.reduce((m, p) => (p.price > 0 && (!m || p.price < m.price) ? p : m), null),
      };
    });
    const KCOLORS = ["#5f0080", "#8e44ad", "#7b2ff7", "#a55eea", "#6c3483", "#9b59b6"];
    const kurly = (K ? K.collections : []).map((c, i) => {
      const mine = products.filter((p) => p.events.includes(c.event_id));
      const discs = mine.map((p) => p.disc);
      return {
        ...c, kind: "kurly", platform: "뷰티컬리", color: KCOLORS[i % KCOLORS.length],
        image: mine[0]?.img || "", openEnd: !c.end,
        startDate: parseDate(c.start), endDate: c.end ? parseDate(c.end, true) : null,
        avgDisc: discs.length ? Math.round(discs.reduce((a, b) => a + b, 0) / discs.length) : null,
        over50: discs.filter((d) => d >= 50).length,
        cheapest: mine.reduce((m, p) => (p.price > 0 && (!m || p.price < m.price) ? p : m), null),
      };
    });
    const man = manual.map((e) => ({
      ...e, kind: "manual", event_id: e.id, color: PLATFORM_COLORS[e.platform] || "#868e96",
      startDate: parseDate(e.start), endDate: parseDate(e.end, true),
      max_coupon_pct: +e.max_coupon_pct || null, max_discount_pct: +e.max_discount_pct || null,
      product_count: null, avgDisc: null, over50: null, cheapest: null,
    }));
    // 끝난 이벤트는 뒤로, 나머지는 마감 임박순
    // 종료된 이벤트는 숨김 (직접 입력한 이벤트는 저장은 유지, '보기'로 다시 볼 수 있음)
    const all = [...auto, ...kurly, ...man];
    const isEnded = (e) => !e.openEnd && e.endDate && e.endDate < now;
    hiddenEnded = all.filter(isEnded);
    events = all.filter((e) => showEnded || !isEnded(e)).sort((a, b) => {
      const ea = !!(a.endDate && a.endDate < now), eb = !!(b.endDate && b.endDate < now);
      if (ea !== eb) return ea ? 1 : -1;
      const ta = a.endDate ? a.endDate.getTime() : Infinity, tb = b.endDate ? b.endDate.getTime() : Infinity;
      return ta === tb ? 0 : ta < tb ? -1 : 1;
    });
    evById = Object.fromEntries(events.map((e) => [e.event_id, e]));
  }

  const ddayOf = (e) => {
    if (e.openEnd) return { text: "기간 미정", soon: false };
    if (!e.endDate) return null;
    const days = Math.ceil((e.endDate - now) / 86400000);
    if (days < 0) return { text: "종료", soon: false, ended: true };
    if (days === 0) return { text: "오늘 마감", soon: true };
    return { text: `D-${days}`, soon: days <= 2 };
  };
  const shortDate = (d) => (d ? `${d.getMonth() + 1}.${d.getDate()}` : "?");
  const period = (e, short) => e.openEnd
    ? (short ? "종료일 미정" : `${shortDate(e.startDate)} 수집 · 종료일 미정`)
    : `${e.startDate ? shortDate(e.startDate) : ""} ~ ${shortDate(e.endDate)}`;

  // ---- 상단 ----
  function renderHero() {
    $("#updated").textContent = `지그재그 ${D.updated}` + (K ? ` · 뷰티컬리 ${K.updated}` : "");
    const live = events.filter((e) => !ddayOf(e)?.ended);
    const maxCoupon = Math.max(0, ...live.map((e) => e.max_coupon_pct || 0));
    const platforms = new Set(live.map((e) => e.platform)).size;
    $("#heroSub").textContent =
      `${platforms}개 앱 · 이벤트 ${live.length}개 · 상품 ${products.length.toLocaleString()}개` +
      (maxCoupon ? ` · 최대 ${maxCoupon}% 쿠폰` : "");
  }

  // ---- 이벤트 카드 ----
  const state = { source: "", events: new Set(), q: "", cat1: "", cat2: "", minDisc: 0, maxPrice: 0, sort: "disc", freeShip: false, hasReview: false, limit: 60 };

  function benefitTags(e) {
    const t = [];
    if (e.max_coupon_pct) t.push(`<span class="tag">${e.max_coupon_pct}% 쿠폰</span>`);
    if (e.max_discount_pct) t.push(`<span class="tag">~${e.max_discount_pct}% 할인</span>`);
    if (e.free_shipping) t.push(`<span class="tag plain">무료배송</span>`);
    return t.join("");
  }

  // ---- 달력 ----
  const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const sameDay = (a, b) => a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const activeOn = (e, d) => {
    const s = dayStart(d), en = new Date(s.getTime() + 86400000 - 1);
    // 종료일 미정(뷰티컬리)은 수집일~오늘까지만 표시해 미래 날짜에 진행 중인 것처럼 보이지 않게 함
    const end = e.openEnd ? new Date(Math.max(dayStart(now).getTime() + 86400000 - 1, e.startDate?.getTime() || 0)) : e.endDate;
    return (!e.startDate || e.startDate <= en) && (!end || end >= s);
  };
  const cal = { month: new Date(now.getFullYear(), now.getMonth(), 1), sel: null };

  function renderCalendar() {
    const y = cal.month.getFullYear(), m = cal.month.getMonth();
    $("#calTitle").textContent = `${y}년 ${m + 1}월`;
    const first = new Date(y, m, 1).getDay(), days = new Date(y, m + 1, 0).getDate();
    const today = dayStart(now);
    let html = "<span></span>".repeat(first);
    for (let d = 1; d <= days; d++) {
      const date = new Date(y, m, d);
      const act = events.filter((e) => activeOn(e, date));
      const ends = act.filter((e) => sameDay(e.endDate, date));
      const dots = act.slice(0, 4).map((e) => `<i style="background:${e.color}"></i>`).join("") + (act.length > 4 ? `<em>+${act.length - 4}</em>` : "");
      const cls = ["cal-day", date.getDay() === 0 && "sun", date < today && "past", sameDay(date, today) && "today", sameDay(date, cal.sel) && "sel"].filter(Boolean).join(" ");
      const tip = act.length ? act.map((e) => `${sameDay(e.endDate, date) ? "[마감] " : ""}${e.platform} ${e.title}`).join("\n") : "진행 중인 이벤트 없음";
      html += `<button class="${cls}" data-d="${d}" title="${esc(tip)}"><span class="n">${d}</span>
        <span class="cal-dots">${dots}</span>${ends.length ? `<span class="cal-end">마감${ends.length > 1 ? ends.length : ""}</span>` : ""}</button>`;
    }
    $("#calDays").innerHTML = html;
    renderCalInfo();
  }

  function renderCalInfo() {
    const d = cal.sel || now;
    const label = cal.sel ? `${d.getMonth() + 1}월 ${d.getDate()}일` : `오늘(${d.getMonth() + 1}.${d.getDate()})`;
    const act = events.filter((e) => activeOn(e, d));
    const ends = act.filter((e) => sameDay(e.endDate, d));
    // 오늘 기준이면 이번 주(7일 내) 마감 예정도 같이 안내
    const soon = cal.sel ? [] : events.filter((e) => { const dd = ddayOf(e); return dd && !dd.ended && !e.openEnd && e.endDate - now < 7 * 86400000; });
    const list = (cal.sel ? act : soon).slice(0, 5);
    $("#calInfo").innerHTML =
      `<div>${label} 진행 중 <b>${act.length}개</b>${ends.length ? ` · 마감 <b style="color:var(--warn)">${ends.length}개</b>` : ""}
        ${cal.sel ? ` · <button class="linkbtn" id="calClear">선택 해제</button>` : ""}</div>` +
      (list.length ? `<div style="margin-top:6px">${cal.sel ? "이날 진행 중" : "7일 안에 마감"}</div><ul>${list.map((e) =>
        `<li><span class="ev-dot" style="background:${e.color}"></span>${esc(e.title)}
          ${sameDay(e.endDate, d) || !cal.sel ? `<span class="end-tag">${sameDay(e.endDate, d) ? "오늘 마감" : ddayOf(e).text}</span>` : ""}
          ${e.openEnd ? `<span class="hint" style="font-size:11px">종료일 미정</span>` : ""}</li>`).join("")}
          ${(cal.sel ? act : soon).length > 5 ? `<li style="color:var(--muted)">외 ${(cal.sel ? act : soon).length - 5}개</li>` : ""}</ul>` : "");
  }

  $("#calPrev").addEventListener("click", () => { cal.month = new Date(cal.month.getFullYear(), cal.month.getMonth() - 1, 1); renderCalendar(); });
  $("#calNext").addEventListener("click", () => { cal.month = new Date(cal.month.getFullYear(), cal.month.getMonth() + 1, 1); renderCalendar(); });
  $("#calDays").addEventListener("click", (ev) => {
    const b = ev.target.closest(".cal-day");
    if (!b) return;
    const d = new Date(cal.month.getFullYear(), cal.month.getMonth(), +b.dataset.d);
    cal.sel = sameDay(d, cal.sel) ? null : d;
    renderCalendar(); renderEvents();
  });
  $("#calInfo").addEventListener("click", (ev) => {
    if (ev.target.id === "calClear") { cal.sel = null; renderCalendar(); renderEvents(); }
  });

  // ---- 이벤트 목록 ----
  function renderEvents() {
    const list = cal.sel ? events.filter((e) => activeOn(e, cal.sel)) : events;
    const endedNote = hiddenEnded.length
      ? ` · <button class="linkbtn" id="toggleEnded">${showEnded ? "종료된 이벤트 숨기기" : `종료된 이벤트 ${hiddenEnded.length}개 보기`}</button>` : "";
    $("#evFilterInfo").innerHTML = (cal.sel
      ? `${cal.sel.getMonth() + 1}월 ${cal.sel.getDate()}일 진행 중 ${list.length}개`
      : "누르면 그 이벤트 상품만 보여요") + endedNote;
    $("#events").innerHTML = list.length ? list.map((e) => {
      const dd = ddayOf(e);
      const badge = dd ? `<span class="dday ${dd.soon ? "soon" : ""}">${dd.text}</span>` : "";
      const link = safeUrl(e.url) ? `<a class="ev-link" href="${esc(e.url)}" target="_blank" rel="noopener">보기 ↗</a>` : "";
      const cls = ["ev", e.kind === "manual" && "manual", state.events.has(e.event_id) && "on", dd?.ended && "ended"].filter(Boolean).join(" ");
      const img = e.kind === "manual"
        ? `<div class="ev-img" style="background:${e.color}">${esc(e.platform)}${badge}</div>`
        : `<div class="ev-img" style="background-image:url('${esc(e.image)}')">${badge}</div>`;
      const stats = e.kind === "manual"
        ? `<span class="badge-manual" style="margin:0">직접 입력</span><span class="edit-link">수정</span>`
        : `<span>상품 <b>${e.product_count.toLocaleString()}</b></span><span>평균 <b>${e.avgDisc ?? "-"}%</b></span>`;
      return `<button class="${cls}" data-id="${esc(e.event_id)}" title="${esc(e.description || e.scope || "")}">
        ${img}
        <div class="ev-body">
          <div class="ev-top"><span class="ev-dot" style="background:${e.color}"></span><span class="platform">${esc(e.platform)}</span><span class="ev-period">${period(e, true)}</span></div>
          <div class="ev-title">${esc(e.title)}</div>
          <div class="tags">${benefitTags(e)}</div>
          ${e.kind === "manual" && e.scope ? `<div class="manual-note">적용: <b>${esc(e.scope)}</b></div>` : ""}
          <div class="ev-stats">${stats}${link}</div>
        </div>
      </button>`;
    }).join("") : `<div class="empty" style="padding:20px 0">이날 진행 중인 이벤트가 없어요</div>`;
  }
  $("#evFilterInfo").addEventListener("click", (ev) => {
    if (ev.target.id === "toggleEnded") { showEnded = !showEnded; refresh(); }
  });
  $("#events").addEventListener("click", (ev) => {
    if (ev.target.closest("a")) return;
    const card = ev.target.closest(".ev");
    if (!card) return;
    const e = evById[card.dataset.id];
    if (e.kind === "manual") return openForm(e);
    toggleEvent(e.event_id, true);
    $("#explore").scrollIntoView({ behavior: "smooth" });
  });

  // ---- 비교 표 ----
  function renderTable() {
    const best = (k) => Math.max(0, ...events.filter((e) => !ddayOf(e)?.ended).map((e) => e[k] || 0));
    const bc = best("max_coupon_pct"), bd = best("avgDisc");
    const na = `<span class="hint">-</span>`;
    $("#cmpTable").innerHTML = `
      <thead><tr><th>이벤트</th><th>기간</th><th>남은 기간</th><th>쿠폰</th><th>최대 할인</th><th>적용 대상 / 혜택</th><th>상품 수</th><th>평균 할인</th><th>50%↑ 상품</th><th>최저가 상품</th></tr></thead>
      <tbody>${events.map((e) => {
        const dd = ddayOf(e);
        const scope = e.kind === "manual" ? (e.scope || "-") : (e.description || "-");
        return `<tr style="${dd?.ended ? "opacity:.5" : ""}">
          <td><span class="dot" style="background:${e.color}"></span><span class="platform">${esc(e.platform)}</span> <b>${esc(e.title)}</b>${e.kind === "manual" ? '<span class="badge-manual">직접 입력</span>' : ""}</td>
          <td>${period(e)}</td>
          <td>${dd ? `<span class="${dd.soon ? "best" : ""}">${dd.text}</span>` : "-"}</td>
          <td class="${e.max_coupon_pct && e.max_coupon_pct === bc ? "best" : ""}">${e.max_coupon_pct ? `최대 ${e.max_coupon_pct}%` : "-"}</td>
          <td>${e.max_discount_pct ? `${e.max_discount_pct}%` : "-"}</td>
          <td>${esc(scope)}</td>
          <td class="num">${e.product_count != null ? e.product_count.toLocaleString() : na}</td>
          <td class="num ${e.avgDisc && e.avgDisc === bd ? "best" : ""}">${e.avgDisc != null ? `<span class="bar" style="width:${e.avgDisc}px"></span>${e.avgDisc}%` : na}</td>
          <td class="num">${e.over50 != null ? e.over50.toLocaleString() + "개" : na}</td>
          <td>${e.cheapest ? `<a href="${esc(e.cheapest.url)}" target="_blank" rel="noopener">${won(e.cheapest.price)}</a>` : na}</td>
        </tr>`;
      }).join("")}</tbody>`;
  }

  // ---- 입력 폼 ----
  const dlg = $("#evDialog"), form = $("#evForm");
  let editingId = null;
  function openForm(e) {
    editingId = e ? e.id : null;
    form.reset();
    $("#evFormTitle").textContent = e ? "이벤트 수정" : "이벤트 직접 추가";
    $("#evDelete").style.display = e ? "" : "none";
    if (e) {
      for (const el of form.elements) {
        if (!el.name) continue;
        if (el.type === "checkbox") el.checked = !!e[el.name];
        else el.value = e[el.name] ?? "";
      }
    }
    dlg.showModal();
  }
  $("#addEvent").addEventListener("click", () => openForm(null));
  $("#evCancel").addEventListener("click", () => dlg.close());
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const fd = new FormData(form);
    const rec = Object.fromEntries([...fd.entries()].map(([k, v]) => [k, String(v).trim()]));
    rec.free_shipping = fd.has("free_shipping");
    if (rec.start && rec.end && rec.start > rec.end) return alert("종료일이 시작일보다 빨라요.");
    if (editingId) {
      manual = manual.map((m) => (m.id === editingId ? { ...rec, id: editingId } : m));
    } else {
      manual.push({ ...rec, id: "m_" + Date.now().toString(36) });
    }
    saveManual(manual);
    dlg.close();
    refresh();
  });
  $("#evDelete").addEventListener("click", () => {
    if (!editingId || !confirm("이 이벤트를 삭제할까요?")) return;
    manual = manual.filter((m) => m.id !== editingId);
    saveManual(manual);
    dlg.close();
    refresh();
  });

  // 내보내기 / 가져오기
  $("#exportEv").addEventListener("click", () => {
    if (!manual.length) return alert("직접 입력한 이벤트가 아직 없어요.");
    const blob = new Blob([JSON.stringify(manual, null, 2)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `딜레이더_직접입력이벤트_${new Date().toISOString().slice(0, 10)}.json` });
    a.click();
    URL.revokeObjectURL(a.href);
  });
  $("#importEv").addEventListener("change", async (ev) => {
    const file = ev.target.files[0];
    ev.target.value = "";
    if (!file) return;
    try {
      const list = JSON.parse(await file.text());
      if (!Array.isArray(list)) throw new Error();
      const ids = new Set(manual.map((m) => m.id));
      const added = list.filter((m) => m && m.title && m.platform && !ids.has(m.id))
        .map((m) => ({ ...m, id: m.id || "m_" + Math.random().toString(36).slice(2) }));
      manual = manual.concat(added);
      saveManual(manual);
      refresh();
      alert(`${added.length}개 이벤트를 불러왔어요.`);
    } catch {
      alert("파일 형식이 올바르지 않아요. 내보내기로 만든 .json 파일을 선택하세요.");
    }
  });

  // ---- 상품 필터 ----
  const cat1s = [...new Set(products.map((p) => p.cat1).filter(Boolean))].sort();
  $("#cat1").insertAdjacentHTML("beforeend", cat1s.map((c) => `<option>${esc(c)}</option>`).join(""));
  const singleCat = cat1s.length === 1 ? cat1s[0] : "";
  if (singleCat) {
    $("#cat1").style.display = "none";
    document.querySelector(".hero em").textContent = `${singleCat} 혜택만`;
    document.title = `딜레이더 · ${singleCat} 이벤트 혜택 한눈에`;
  }

  function renderChips() {
    const autos = events.filter((e) => e.kind !== "manual" && (!state.source || (state.source === "kurly") === (e.kind === "kurly")));
    $("#eventChips").innerHTML =
      `<button class="chip ${state.events.size === 0 ? "on" : ""}" data-id="">전체 이벤트</button>` +
      autos.map((e) => `<button class="chip ${state.events.has(e.event_id) ? "on" : ""}" data-id="${esc(e.event_id)}">
        <span class="dot" style="background:${e.color}"></span>${esc(e.title)}</button>`).join("");
  }
  function toggleEvent(id, only) {
    if (!id) state.events.clear();
    else if (only) { const had = state.events.has(id) && state.events.size === 1; state.events.clear(); if (!had) state.events.add(id); }
    else state.events.has(id) ? state.events.delete(id) : state.events.add(id);
    // 뷰티컬리 상품은 세부 카테고리가 없으므로 컬리 기획전을 고르면 세부 카테고리 필터 해제
    if (state.cat2 && evById[id]?.kind === "kurly" && state.events.has(id)) { state.cat2 = ""; $("#cat2").value = ""; }
    state.limit = 60;
    renderEvents(); renderChips(); renderKurly(); renderGrid();
  }
  $("#eventChips").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (c) toggleEvent(c.dataset.id, false);
  });

  const bind = (id, key, fn = (el) => el.value) =>
    $(id).addEventListener("change", (e) => { state[key] = fn(e.target); state.limit = 60; if (key === "cat1") fillCat2(); renderGrid(); });
  let qTimer;
  $("#q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { state.q = e.target.value.trim().toLowerCase(); state.limit = 60; renderGrid(); }, 150); });
  bind("#cat1", "cat1");
  bind("#cat2", "cat2");
  bind("#minDisc", "minDisc", (el) => +el.value);
  bind("#maxPrice", "maxPrice", (el) => +el.value);
  bind("#sort", "sort");
  bind("#freeShip", "freeShip", (el) => el.checked);
  bind("#hasReview", "hasReview", (el) => el.checked);
  bind("#lowOnly", "lowOnly", (el) => el.checked);
  bind("#couponFilter", "couponFilter");

  // ---- 쿠폰 ----
  // 표시 가격(price)은 쿠폰이 적용된 최종 혜택가. preCoupon > price 이면 쿠폰이 반영된 가격.
  const hasCoupon = (p) => p.preCoupon && p.preCoupon > p.price;
  const shortWon = (n) => (n >= 10000 && n % 10000 === 0 ? `${n / 10000}만원` : won(n));
  function couponLine(p) {
    if (!hasCoupon(p)) return "";
    const cond = p.couponMin && p.couponMin > 1000 ? ` (${shortWon(p.couponMin)} 이상)` : "";
    // 쿠폰 최소 구매금액이 이 상품 쿠폰 전 가격보다 크면 1개만 사서는 쿠폰 조건 미달
    const warn = p.couponMin && p.couponMin > p.preCoupon
      ? ` <span class="cp-warn" title="이 상품 하나만 사면 쿠폰 최소 구매금액에 못 미쳐요">· 1개만 사면 ${won(p.preCoupon)}</span>` : "";
    const end = p.couponEnd ? ` · ${md(p.couponEnd)}까지` : "";
    return `<div class="cp-line">쿠폰 전 <s>${won(p.preCoupon)}</s> · ${esc(p.couponLabel || "쿠폰")}${cond}${end}${warn}</div>`;
  }

  // ---- 가격 기록 판정 ----
  // 최저가는 '우리가 수집한 기록' 기준. 기록이 2일 이상 쌓여야 판정.
  const md = (s) => (s ? `${+s.slice(5, 7)}.${+s.slice(8, 10)}` : "");
  function priceVerdict(p) {
    if (!p.days || p.days < 2 || !p.minPrice) return { type: "new", html: `가격 기록 ${p.days || 1}일째` };
    if (p.price <= p.minPrice) return { type: "low", html: "기록상 최저가" };
    const diff = p.price - p.minPrice;
    return { type: "higher", html: `최저 ${won(p.minPrice)}(${md(p.minDate)})보다 <b>+${won(diff)}</b>` };
  }
  const since = [D.historySince, K?.historySince].filter(Boolean).sort()[0];
  $("#historyNote").textContent = "표시 가격은 쿠폰까지 적용된 최종 혜택가예요 (쿠폰 다운로드·최소 구매금액 조건 필요, 개인 쿠폰·카드 할인 제외). " + (since
    ? `가격 판정은 ${md(since)}부터 매일 수집한 기록 기준이에요 (지그재그·컬리의 실제 역대가와 다를 수 있어요). 기록이 2일 이상 쌓인 상품부터 판정해요.`
    : "");

  function fillCat2() {
    state.cat2 = "";
    const sel = $("#cat2");
    const list = [...new Set(products.filter((p) => p.cat1 === state.cat1).map((p) => p.cat2).filter(Boolean))].sort();
    sel.innerHTML = `<option value="">${singleCat ? "전체 카테고리" : "세부 카테고리"}</option>` + list.map((c) => `<option>${esc(c)}</option>`).join("");
    sel.disabled = !state.cat1;
  }
  if (singleCat) { state.cat1 = singleCat; fillCat2(); }

  // ---- 상품 그리드 ----
  const sorters = {
    disc: (a, b) => b.disc - a.disc || a.price - b.price,
    price: (a, b) => a.price - b.price,
    reviews: (a, b) => b.reviews - a.reviews,
    score: (a, b) => b.score - a.score || b.reviews - a.reviews,
    multi: (a, b) => b.events.length - a.events.length || b.disc - a.disc,
    nearLow: (a, b) => (a.days >= 2 ? a.price / a.minPrice : 9) - (b.days >= 2 ? b.price / b.minPrice : 9) || b.disc - a.disc,
  };

  function filtered() {
    const s = state;
    return products.filter((p) =>
      (!s.source || p.source === s.source) &&
      (s.events.size === 0 || p.events.some((id) => s.events.has(id))) &&
      (!s.q || p.title.toLowerCase().includes(s.q) || (p.shop || "").toLowerCase().includes(s.q)) &&
      (!s.cat1 || p.cat1 === s.cat1) &&
      (!s.cat2 || p.cat2 === s.cat2) &&
      p.disc >= s.minDisc &&
      (!s.maxPrice || (p.price > 0 && p.price <= s.maxPrice)) &&
      (!s.freeShip || p.freeShip) &&
      (!s.hasReview || p.reviews >= 100) &&
      (!s.lowOnly || priceVerdict(p).type === "low") &&
      (!s.couponFilter || (s.couponFilter === "coupon" ? hasCoupon(p) : !hasCoupon(p) && p.preCoupon != null))
    ).sort(sorters[s.sort]);
  }

  const SRC_LABEL = { zigzag: "지그재그", kurly: "뷰티컬리" };
  function card(p) {
    const evs = p.events.map((id) => evById[id]).filter(Boolean);
    const review = p.source === "kurly"
      ? (p.reviewsText ? `<span>리뷰 ${esc(p.reviewsText)}</span>` : "")
      : (p.reviews ? `<span><span class="star">★</span> ${p.score} (${p.reviews.toLocaleString()})</span>` : "");
    return `<div class="card ${p.source}">
      <a class="card-link" href="${esc(p.url)}" target="_blank" rel="noopener" title="${SRC_LABEL[p.source]}에서 보기">
      <div class="thumb"><img loading="lazy" src="${esc(p.img)}" alt="">
        <span class="src-badge ${p.source}">${SRC_LABEL[p.source]}</span>
        ${evs.length > 1 ? `<span class="multi">${evs.length}개 이벤트 노출</span>` : ""}</div>
      <div class="shop">${esc(p.shop)}</div>
      <div class="ptitle">${esc(p.title)}</div>
      <div class="price-row">${p.disc ? `<span class="disc">${p.disc}%</span>` : ""}<span class="price">${won(p.price)}</span>
        ${p.orig > p.price ? `<span class="orig">${won(p.orig)}</span>` : ""}${hasCoupon(p) ? `<span class="cp-badge">쿠폰가</span>` : ""}</div>
      ${couponLine(p)}
      </a>
      ${(() => { const v = priceVerdict(p); return `<button type="button" class="pv ${v.type}" data-hist="${esc(p.source)}:${esc(p.id)}" title="가격 기록 그래프 보기">${v.html}</button>`; })()}
      <div class="meta">${p.coupon && !(hasCoupon(p) && /^\+?\d+%\s*쿠폰$/.test(p.coupon.trim())) ? `<span class="coupon">${esc(p.coupon)}</span>` : ""}${review}
        ${p.freeShip ? `<span class="free">무료배송</span>` : ""}${p.soldOut ? `<span style="color:var(--warn)">품절</span>` : ""}</div>
      <div class="ev-tags" title="이 상품이 노출된 이벤트·기획전 (쿠폰 적용 조건은 상품 페이지에서 확인)">${evs.map((e) => `<span class="ev-tag" style="background:${e.color}">${esc(e.title)}</span>`).join("")}</div>
    </div>`;
  }

  // ---- 가격 기록 창 + 그래프 ----
  const H = window.PRICE_HISTORY;
  const byKey = Object.fromEntries(products.map((p) => [`${p.source}:${p.id}`, p]));
  const fmtD = (d) => `${d.getMonth() + 1}.${d.getDate()}`;
  const compact = (n) => (n >= 10000 ? `${+(n / 10000).toFixed(n % 10000 ? 1 : 0)}만` : n.toLocaleString());

  function historyOf(p) {
    const rows = H?.[p.source]?.[p.id] || [];
    const base = new Date(H?.d0 + "T00:00:00");
    return rows.map(([off, price, pre]) => ({ date: new Date(base.getTime() + off * 86400000), price, pre: pre || null }));
  }

  function niceTicks(lo, hi, count = 4) {
    const span = hi - lo || hi || 1;
    const raw = span / count, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    const start = Math.floor(lo / step) * step, ticks = [];
    for (let v = start; v <= hi + step * 0.001; v += step) ticks.push(Math.round(v));
    return ticks;
  }

  function drawChart(pts) {
    const box = $("#hChart"), tip = $("#hTip");
    tip.hidden = true;
    const hasPre = pts.some((d) => d.pre);
    $("#hLegend").innerHTML = hasPre
      ? `<span><i class="lkey" style="background:var(--series-1)"></i>쿠폰 적용가</span><span><i class="lkey" style="background:var(--series-2)"></i>쿠폰 전 가격</span>`
      : "";
    $("#hChartTitle").textContent = hasPre ? "가격 변화" : "가격 변화 (쿠폰 적용가)";
    if (pts.length < 2) {
      box.innerHTML = `<div class="hempty">기록이 2일 이상 쌓이면 그래프가 나와요.<br>지금은 ${pts.length ? fmtD(pts[0].date) + " 기록 1개" : "기록이 없어요"} · 매일 자동으로 쌓여요</div>`;
      return;
    }
    const W = Math.max(300, box.clientWidth || 560), Hh = 230;
    const m = { l: 52, r: 70, t: 18, b: 26 };
    const vals = pts.flatMap((d) => [d.price, d.pre].filter(Boolean));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.12 || hi * 0.05;
    const ticks = niceTicks(lo - pad, hi + pad);
    lo = ticks[0]; hi = ticks[ticks.length - 1];
    const t0 = pts[0].date.getTime(), t1 = pts[pts.length - 1].date.getTime();
    const x = (d) => m.l + ((d.date.getTime() - t0) / (t1 - t0)) * (W - m.l - m.r);
    const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (Hh - m.t - m.b);
    const path = (key) => {
      let s = "", pen = false;
      pts.forEach((d) => { if (d[key] == null) { pen = false; return; } s += `${pen ? "L" : "M"}${x(d).toFixed(1)},${y(d[key]).toFixed(1)}`; pen = true; });
      return s;
    };
    const minV = Math.min(...pts.map((d) => d.price));
    const minIdx = pts.map((d) => d.price).lastIndexOf(minV);
    const last = pts[pts.length - 1], lastIsMin = minIdx === pts.length - 1;
    const showDots = pts.length <= 31;
    const dot = (d, key, color, r = 4) => `<circle cx="${x(d)}" cy="${y(d[key])}" r="${r}" fill="${color}" stroke="var(--surface-1)" stroke-width="2"/>`;
    // x축 날짜 라벨: 처음·끝 + 공간이 되면 중간
    const xl = [pts[0], last];
    if (pts.length > 2 && W > 380) xl.splice(1, 0, pts[Math.floor((pts.length - 1) / 2)]);
    const grid = ticks.map((v) => `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)" stroke-width="1"/>
      <text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--text-muted)">${compact(v)}</text>`).join("");
    box.innerHTML = `<svg viewBox="0 0 ${W} ${Hh}" role="img" aria-label="가격 변화: ${fmtD(pts[0].date)}부터 ${fmtD(last.date)}까지 ${pts.length}일, 최저 ${won(minV)}, 현재 ${won(last.price)}">
      ${grid}
      ${xl.map((d) => `<text x="${x(d)}" y="${Hh - 6}" text-anchor="middle" font-size="11" fill="var(--text-muted)">${fmtD(d.date)}</text>`).join("")}
      <line x1="${m.l}" x2="${W - m.r}" y1="${y(minV)}" y2="${y(minV)}" stroke="var(--text-muted)" stroke-width="1" opacity=".55"/>
      ${hasPre ? `<path d="${path("pre")}" fill="none" stroke="var(--series-2)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` : ""}
      <path d="${path("price")}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${showDots && hasPre ? pts.filter((d) => d.pre).map((d) => dot(d, "pre", "var(--series-2)", 3)).join("") : ""}
      ${showDots ? pts.map((d) => dot(d, "price", "var(--series-1)", 3)).join("") : ""}
      ${dot(pts[minIdx], "price", "var(--series-1)", 5)}${dot(last, "price", "var(--series-1)", 5)}
      <text x="${W - m.r + 8}" y="${y(last.price) + 4}" font-size="12" font-weight="700" fill="var(--text)">${last.price.toLocaleString()}원</text>
      <text x="${W - m.r + 8}" y="${y(last.price) + 18}" font-size="10.5" fill="var(--text-muted)">${lastIsMin ? "오늘·최저" : "오늘"}</text>
      ${lastIsMin ? "" : `<text x="${Math.min(Math.max(x(pts[minIdx]), m.l + 40), W - m.r - 40)}" y="${y(minV) + 18}" text-anchor="middle" font-size="11" fill="var(--text-secondary)">최저 ${won(minV)} (${fmtD(pts[minIdx].date)})</text>`}
      <line id="hCross" x1="0" x2="0" y1="${m.t}" y2="${Hh - m.b}" stroke="var(--text-muted)" stroke-width="1" visibility="hidden"/>
      <rect id="hHit" x="${m.l}" y="0" width="${W - m.l - m.r}" height="${Hh}" fill="transparent"/>
    </svg>`;
    // 크로스헤어 + 툴팁: 가장 가까운 날짜에 맞춤
    const svg = box.querySelector("svg"), cross = svg.querySelector("#hCross");
    const move = (ev) => {
      const r = svg.getBoundingClientRect(), sx = ((ev.clientX - r.left) / r.width) * W;
      let best = 0;
      pts.forEach((d, i) => { if (Math.abs(x(d) - sx) < Math.abs(x(pts[best]) - sx)) best = i; });
      const d = pts[best], px = x(d);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      tip.innerHTML = `<div class="d">${d.date.getFullYear()}.${fmtD(d.date)}${best === minIdx ? " · 기록상 최저" : ""}</div>
        <div class="r"><i class="lkey" style="background:var(--series-1)"></i>쿠폰 적용가 <b>${won(d.price)}</b></div>
        ${d.pre ? `<div class="r"><i class="lkey" style="background:var(--series-2)"></i>쿠폰 전 ${won(d.pre)}</div>` : ""}`;
      tip.hidden = false;
      const wrap = $("#hViz").getBoundingClientRect(), cx = r.left - wrap.left + (px / W) * r.width;
      tip.style.left = `${Math.min(Math.max(cx - tip.offsetWidth / 2, 0), wrap.width - tip.offsetWidth)}px`;
      tip.style.top = `${r.top - wrap.top + 4}px`;
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  }

  function openHistory(p) {
    const pts = historyOf(p);
    const v = priceVerdict(p);
    $("#hShop").textContent = `${SRC_LABEL[p.source]} · ${p.shop || ""}`;
    $("#hTitle").textContent = p.title;
    $("#hLink").href = safeUrl(p.url);
    const maxV = pts.length ? Math.max(...pts.map((d) => d.price)) : p.price;
    const minP = pts.length ? pts.reduce((a, d) => (d.price <= a.price ? d : a), pts[0]) : null;
    $("#hStats").innerHTML = `
      <div class="hstat"><div class="l">오늘 가격</div><div class="v">${won(p.price)}</div><div class="s">${hasCoupon(p) ? "쿠폰 적용가" : "&nbsp;"}</div></div>
      <div class="hstat"><div class="l">기록상 최저가</div><div class="v">${minP ? won(minP.price) : "-"}</div><div class="s">${minP ? fmtD(minP.date) : "&nbsp;"}</div></div>
      <div class="hstat"><div class="l">기록상 최고가</div><div class="v">${won(maxV)}</div><div class="s">&nbsp;</div></div>
      <div class="hstat"><div class="l">판정</div><div class="v" style="font-size:13px;line-height:1.4">${v.type === "low" ? "✅ 기록상 최저가" : v.type === "new" ? "기록 쌓는 중" : `최저가보다 +${won(p.price - p.minPrice)}`}</div><div class="s">${pts.length}일 기록</div></div>`;
    $("#hTable").innerHTML = `<thead><tr><th>날짜</th><th>쿠폰 적용가</th><th>쿠폰 전 가격</th><th>전날 대비</th></tr></thead><tbody>` +
      pts.map((d, i) => { const diff = i ? d.price - pts[i - 1].price : 0;
        return `<tr><td>${d.date.getFullYear()}.${fmtD(d.date)}</td><td class="num">${won(d.price)}</td><td class="num">${d.pre ? won(d.pre) : "-"}</td>
          <td class="num">${i === 0 ? "-" : diff === 0 ? "변동 없음" : `${diff > 0 ? "▲ +" : "▼ "}${won(diff)}`}</td></tr>`; }).reverse().join("") + "</tbody>";
    $("#hNote").textContent = `${H?.d0 ? md(H.d0) + "부터 " : ""}매일 수집한 기록 기준 (사이트의 실제 역대가와 다를 수 있어요)`;
    $("#histDialog").showModal();
    drawChart(pts);
  }
  $("#grid").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-hist]");
    if (!b) return;
    const p = byKey[b.dataset.hist];
    if (p) openHistory(p);
  });
  $("#hClose").addEventListener("click", () => $("#histDialog").close());
  $("#histDialog").addEventListener("click", (ev) => { if (ev.target.id === "histDialog") $("#histDialog").close(); });

  // 출처 필터 (전체 / 지그재그 / 뷰티컬리)
  $("#sourceSeg").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    state.source = b.dataset.src;
    document.querySelectorAll("#sourceSeg button").forEach((x) => x.classList.toggle("on", x === b));
    // 다른 출처의 이벤트 선택은 해제
    [...state.events].forEach((id) => { const e = evById[id]; if (state.source && e && (e.kind === "kurly") !== (state.source === "kurly")) state.events.delete(id); });
    state.limit = 60;
    renderChips(); renderEvents(); renderKurly(); renderGrid();
  });

  // ---- 뷰티컬리 기획전 섹션 ----
  function renderKurly() {
    const list = events.filter((e) => e.kind === "kurly");
    $("#kurlySection").hidden = !list.length;
    if (!list.length) return;
    $("#kurlyInfo").textContent = `${K.updated} 수집 · 메인에 노출된 기획전과 기획전별 앞쪽 상품(최대 20개)만 보여요`;
    $("#kurlyList").innerHTML = list.map((e) => {
      const ps = products.filter((p) => p.events.includes(e.event_id));
      return `<div class="kc ${state.events.has(e.event_id) ? "on" : ""}">
        <div class="kc-title">${esc(e.title)}</div>
        ${e.description ? `<div class="kc-desc">${esc(e.description)}</div>` : ""}
        <div class="tags">${benefitTags(e)}${e.openEnd ? `<span class="tag plain">종료일 미정</span>` : ""}</div>
        <div class="kc-thumbs">${ps.slice(0, 6).map((p) => `<img loading="lazy" src="${esc(p.img)}" alt="" title="${esc(p.title)}">`).join("")}</div>
        <div class="kc-actions">
          <button class="btn" data-kc="${esc(e.event_id)}">${state.events.has(e.event_id) ? "선택 해제" : `상품 ${ps.length}개 보기`}</button>
          <span class="hint">평균 ${e.avgDisc ?? "-"}% 할인</span>
          ${safeUrl(e.url) ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">컬리에서 보기 ↗</a>` : ""}
        </div>
      </div>`;
    }).join("");
  }
  $("#kurlyList").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-kc]");
    if (!b) return;
    toggleEvent(b.dataset.kc, true);
    renderKurly();
    if (state.events.has(b.dataset.kc)) $("#explore").scrollIntoView({ behavior: "smooth" });
  });

  function renderGrid() {
    const list = filtered();
    const cat2Note = state.cat2 && state.source !== "zigzag" && K ? " · 뷰티컬리 상품은 세부 카테고리 정보가 없어 제외돼요" : "";
    const bySrc = list.reduce((m, p) => ((m[p.source] = (m[p.source] || 0) + 1), m), {});
    $("#resultCount").textContent = `${list.length.toLocaleString()}개 상품` +
      (Object.keys(bySrc).length > 1 ? ` (지그재그 ${bySrc.zigzag || 0} · 뷰티컬리 ${bySrc.kurly || 0})` : "") + cat2Note;
    $("#grid").innerHTML = list.length
      ? list.slice(0, state.limit).map(card).join("")
      : `<div class="empty">조건에 맞는 상품이 없어요</div>`;
    $("#more").style.display = list.length > state.limit ? "" : "none";
  }
  $("#more").addEventListener("click", () => { state.limit += 60; renderGrid(); });

  function refresh() {
    buildEvents();
    renderHero(); renderCalendar(); renderEvents(); renderTable(); renderKurly(); renderChips(); renderGrid();
  }
  refresh();
})();
