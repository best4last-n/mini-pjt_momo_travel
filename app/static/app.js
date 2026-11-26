/* 모모의 여행 상담소 — 화면 로직 (빌드 없음, 프레임워크 없음)
 * 구성: 상수·유틸 → 상태 → API → 대화 → 여행 조건 → 브로셔 → 경비 → 정산(공유 페이지) → 지도 → 배치(PC/모바일) → 음성 → 시작
 * 외부 데이터(관광공사·LLM)에서 온 글자는 모두 esc()를 거쳐 넣는다.
 */
(function () {
  "use strict";

  /* ───────── 상수·유틸 ───────── */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const cssEsc = (s) => (window.CSS && CSS.escape ? CSS.escape(String(s)) : String(s).replace(/["\\\]]/g, "\\$&"));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BOOT = window.MOMO_BOOT || {};
  const params = new URLSearchParams(location.search);
  // 모드: 상담(기본) · 공유/전체 화면(읽기 전용) · 데모(?demo=1) · 데모 전체 화면(?demo=1&view=1)
  const DEMO = !BOOT.readOnly && params.get("demo") === "1";
  const DEMO_VIEW = DEMO && params.get("view") === "1";
  const READ_ONLY = !!BOOT.readOnly || DEMO_VIEW;
  const VIEW = DEMO_VIEW || !!(BOOT.payload && BOOT.payload.view);
  const PRINT = READ_ONLY && params.get("print") === "1";
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
  const MQ_PC = window.matchMedia("(min-width: 1024px)");
  // 테마 값은 tokens.css 에서 읽는다 (브랜드 캐릭터 sprite id, 일자 색)
  const ROOT_CSS = window.getComputedStyle(document.documentElement);
  const cssVar = (name, fb) => ((ROOT_CSS.getPropertyValue(name) || "").trim().replace(/^["']|["']$/g, "") || fb);
  const BRAND_SPRITE = cssVar("--brand-sprite", "momo-bird");
  const DAY_COLORS = ["#3a5a8c", "#d0703a", "#2f8a5b", "#7a5ab0", "#b0466a", "#3b8c8c", "#8a6d3b", "#5a6fb0", "#6a8a3a"].map((fb, i) => cssVar(`--day-${i + 1}`, fb));
  const WD = "일월화수목금토";
  const LICENSE = { Type1: "공공누리 제1유형", Type3: "공공누리 제3유형(변경 금지)" };
  const STATUS = {
    confirmed: { t: "확정", c: "ok" }, assumed: { t: "가정", c: "warn" },
    missing: { t: "미정", c: "bad" }, optional: { t: "선택", c: "off" },
  };
  const TYPE_LABEL = { place: "관광", meal: "식사", stay: "숙박", move: "이동" };
  const COST_CATS = ["장거리 교통", "현지 교통", "유류비", "통행료", "주차비", "렌터카", "식비", "카페·디저트", "간식·음료", "입장료", "체험", "기념품", "숙박", "기타"];
  const PLACEHOLDER_CONDS = [
    { key: "region", label: "여행 지역", input: "select", options: ["경주", "순천/구례", "수원"], hint: "지금은 경주 · 순천/구례 · 수원을 꼼꼼히 검증해요." },
    { key: "start_date", label: "출발일", input: "date", options: [], hint: "날짜에 따라 휴무일·운영시간이 달라져요." },
    { key: "length", label: "여행 길이", input: "select", options: ["당일치기", "1박 2일"], hint: "당일~1박 2일까지 꼼꼼히 검증해요." },
    { key: "origin", label: "출발지", input: "text", options: ["서울", "경기 수원", "대전", "광주", "부산", "대구"], hint: "시·군·구까지만 알려 주세요. 오가는 시간을 계산해요." },
    { key: "long_transport", label: "장거리 이동", input: "select", options: ["자차", "KTX·SRT", "고속·시외버스"], hint: "" },
    { key: "local_transport", label: "현지 이동", input: "select", options: ["자차", "렌터카", "대중교통"], hint: "" },
    { key: "intensity", label: "여행 강도", input: "select", options: ["빡빡", "보통", "여유"], hint: "빡빡 하루 4~5곳 · 보통 3~4곳 · 여유 2~3곳" },
    { key: "companions", label: "동반자", input: "select", options: ["보통", "노약자", "어린이·유아"], hint: "노약자는 계단·경사 적은 곳, 어린이는 체험·넓은 야외를 우선해요." },
  ].map((c) => Object.assign({ value: "", status: "missing" }, c));

  const won = (n) => (n == null || isNaN(n) ? "-" : Math.round(Number(n)).toLocaleString("ko-KR") + "원");
  const signedWon = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(Math.round(n || 0)).toLocaleString("ko-KR") + "원";
  function fmtMin(m) {
    m = Math.round(Number(m) || 0);
    if (m < 60) return m + "분";
    const h = Math.floor(m / 60), r = m % 60;
    return r ? `${h}시간 ${r}분` : `${h}시간`;
  }
  function fmtDate(iso, withYear) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    if (isNaN(d)) return esc(iso);
    return (withYear ? d.getFullYear() + "년 " : "") + `${d.getMonth() + 1}월 ${d.getDate()}일(${WD[d.getDay()]})`;
  }
  const km = (v) => (v == null ? "" : (Math.round(v * 10) / 10) + " km");
  const scrollBehavior = () => (REDUCED.matches ? "auto" : "smooth");
  function seasonOf(iso) {
    const m = iso ? Number(String(iso).slice(5, 7)) : 0;
    if (m >= 3 && m <= 5) return "spring";
    if (m >= 6 && m <= 8) return "summer";
    if (m >= 9 && m <= 11) return "autumn";
    return "winter";
  }
  const SEASON_LABEL = { spring: "봄", summer: "여름", autumn: "가을", winter: "겨울" };
  /** 말풍선 글: 이스케이프 후 **굵게**만 허용 */
  const richText = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  /* ───────── SVG ───────── */
  const SPRITE = `<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false"><defs>
<symbol id="momo-bird" viewBox="0 0 64 64">
 <path d="M11 41 L1 33 L3 43 L0 51 L13 47 Z" fill="var(--char-wing)"/>
 <ellipse cx="28" cy="41" rx="20" ry="16" fill="var(--char-body)"/>
 <circle cx="41" cy="28" r="12" fill="var(--char-body)"/>
 <ellipse cx="35" cy="46" rx="12" ry="8" fill="var(--char-belly)"/>
 <path d="M13 39 C17 30 30 32 33 41 C29 47 18 48 13 39Z" fill="var(--char-wing)"/>
 <g transform="rotate(-10 26 47)"><rect x="19" y="42" width="14" height="9.5" rx="1" fill="var(--char-letter)" stroke="var(--char-letter-line)" stroke-width=".9"/>
 <path d="M19.4 42.4 L26 47.3 L32.6 42.4" fill="none" stroke="var(--char-letter-line)" stroke-width=".9"/><circle cx="26" cy="47.4" r="1.5" fill="var(--char-badge)"/></g>
 <circle cx="45" cy="28.5" r="2.5" fill="var(--char-eye)"/><circle cx="45.9" cy="27.6" r=".85" fill="var(--char-belly)"/>
 <circle cx="47.5" cy="33" r="2.4" fill="var(--char-cheek)" opacity=".75"/>
 <path d="M52 27.5 L60 30 L52 32.6Z" fill="var(--char-beak)"/>
 <path d="M29 23.5 C29.5 11 51 10 53 22.5 L53 24.5 L29 24.5Z" fill="var(--char-cap)"/>
 <path d="M44 23 L59 24.6 Q58 27.4 46 26.6Z" fill="var(--char-cap-brim)"/>
 <circle cx="40" cy="17.5" r="2.4" fill="var(--char-badge)"/>
 <path d="M25 56 v4 M33 56 v4" stroke="var(--char-beak)" stroke-width="2" stroke-linecap="round"/>
</symbol>
<symbol id="ico-fork" viewBox="0 0 16 16"><path d="M4.5 1.5v4.2M3 1.5v3.6a1.5 1.5 0 0 0 3 0V1.5M4.5 6.6v7.9M12 1.5c-1.6 1-2.2 3-2.2 5.3h2.2v7.7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-chev" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-send" viewBox="0 0 20 20"><path d="M2.5 9.6 17 3 12.6 17.2l-2.7-5.6zM9.9 11.6 17 3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></symbol>
<symbol id="ico-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></symbol>
<symbol id="ico-pop" viewBox="0 0 16 16"><path d="M9 2.5h4.5V7M13.5 2.5 7.5 8.5M12 9.5V13a.5.5 0 0 1-.5.5h-8A.5.5 0 0 1 3 13V5a.5.5 0 0 1 .5-.5H7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-clock" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 4.6V8l2.3 1.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-car" viewBox="0 0 16 16"><path d="M2.5 10.5V8.2l1.4-3.4A1.5 1.5 0 0 1 5.3 3.9h5.4a1.5 1.5 0 0 1 1.4.9l1.4 3.4v2.3M2 10.5h12v1.7a.8.8 0 0 1-.8.8H2.8a.8.8 0 0 1-.8-.8zM3.5 8h9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><circle cx="4.8" cy="11.7" r=".9" fill="currentColor"/><circle cx="11.2" cy="11.7" r=".9" fill="currentColor"/></symbol>
<symbol id="ico-cam" viewBox="0 0 16 16"><rect x="1.8" y="4.2" width="9" height="7.6" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10.8 7 14.2 5v6l-3.4-2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><circle cx="4.5" cy="6.6" r=".8" fill="currentColor"/></symbol>
<symbol id="ico-home" viewBox="0 0 16 16"><path d="M2.5 7.5 8 3l5.5 4.5M4 6.5V13h3V9.8h2V13h3V6.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></symbol>
<symbol id="ico-pin" viewBox="0 0 16 16"><path d="M8 15s5-4.6 5-8.4A5 5 0 0 0 3 6.6C3 10.4 8 15 8 15z" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="8" cy="6.6" r="1.8" fill="currentColor"/></symbol>
</defs></svg>`;
  const bird = (cls = "bird") => `<svg class="${cls}" aria-hidden="true" focusable="false"><use href="#${BRAND_SPRITE}"/></svg>`;
  const ico = (id, cls = "ico") => `<svg class="${cls}" aria-hidden="true" focusable="false"><use href="#${id}"/></svg>`;
  const MOMO_ILLUST = `<svg class="illust" viewBox="0 0 160 110" role="img" aria-label="사진이 없어 모모 그림으로 대신했어요">
<rect width="160" height="110" fill="var(--season-soft)"/>
<ellipse cx="34" cy="28" rx="18" ry="6" fill="var(--color-surface)" opacity=".9"/><ellipse cx="128" cy="20" rx="14" ry="5" fill="var(--color-surface)" opacity=".9"/>
<path d="M8 92 C40 70 70 96 100 74 S150 60 156 44" fill="none" stroke="var(--season-deep)" stroke-width="1.4" stroke-dasharray="3 4" opacity=".6"/>
<use href="#${BRAND_SPRITE}" x="54" y="22" width="60" height="60"/></svg>`;

  /* 안내 데스크의 모모 — 부위별 그룹(m-*)을 CSS가 상태(data-mood)에 따라 움직인다 */
  const DESK_SVG = `<svg class="desk-svg" viewBox="0 0 150 112" role="img" aria-label="안내 데스크에 앉은 우체부 철새 모모">
<circle cx="86" cy="54" r="38" fill="var(--season-soft)"/>
<g class="m-spark" fill="var(--season-deep)"><path d="M40 30 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2z"/><path d="M132 60 l1.5 4 4 1.5 -4 1.5 -1.5 4 -1.5 -4 -4 -1.5 4 -1.5z"/><path d="M58 14 l1.2 3 3 1.2 -3 1.2 -1.2 3 -1.2 -3 -3 -1.2 3 -1.2z"/></g>
<g transform="translate(46 20)"><g class="m-jump"><g class="m-breathe">
 <path d="M11 41 L1 33 L3 43 L0 51 L13 47 Z" fill="var(--char-wing)"/>
 <g class="m-wing-back"><path d="M30 34 C36 20 50 18 56 22 C50 30 42 38 32 42Z" fill="var(--char-wing)"/></g>
 <ellipse cx="28" cy="41" rx="20" ry="16" fill="var(--char-body)"/>
 <ellipse cx="35" cy="46" rx="12" ry="8" fill="var(--char-belly)"/>
 <g class="m-head">
  <circle cx="41" cy="28" r="12" fill="var(--char-body)"/>
  <g class="m-eye"><circle cx="45" cy="28.5" r="2.5" fill="var(--char-eye)"/><circle cx="45.9" cy="27.6" r=".85" fill="var(--char-belly)"/></g>
  <path class="m-eye-closed" d="M42.4 29 q2.6 2.2 5.2 0" fill="none" stroke="var(--char-eye)" stroke-width="1.5" stroke-linecap="round"/>
  <circle cx="47.5" cy="33" r="2.4" fill="var(--char-cheek)" opacity=".75"/>
  <path d="M52 27.5 L60 30 L52 32.6Z" fill="var(--char-beak)"/>
  <path d="M29 23.5 C29.5 11 51 10 53 22.5 L53 24.5 L29 24.5Z" fill="var(--char-cap)"/>
  <path d="M44 23 L59 24.6 Q58 27.4 46 26.6Z" fill="var(--char-cap-brim)"/>
  <circle cx="40" cy="17.5" r="2.4" fill="var(--char-badge)"/>
 </g>
 <g class="m-wing"><path d="M13 39 C17 30 30 32 33 41 C29 47 18 48 13 39Z" fill="var(--char-wing)"/></g>
</g></g></g>
<g class="m-letters"><rect x="15" y="63" width="30" height="8" rx="1" fill="var(--char-letter)" stroke="var(--char-letter-line)" stroke-width=".8" transform="rotate(-4 30 67)"/>
 <rect x="17" y="58" width="28" height="9" rx="1" fill="var(--char-letter)" stroke="var(--char-letter-line)" stroke-width=".8"/><path d="M17.4 58.4 L31 63.4 L44.6 58.4" fill="none" stroke="var(--char-letter-line)" stroke-width=".8"/>
 <g class="m-mark"><circle cx="31" cy="62.5" r="5.2" fill="none" stroke="var(--char-badge)" stroke-width="1.4"/><text x="31" y="64.6" text-anchor="middle" font-size="5" fill="var(--char-badge)" font-weight="700">확정</text></g></g>
<g class="m-stamp"><rect x="25" y="34" width="12" height="4" rx="1" fill="var(--char-badge)"/><rect x="29" y="22" width="4" height="12" rx="1.5" fill="var(--deco-stamp-handle)"/><circle cx="31" cy="21" r="3.4" fill="var(--deco-stamp-handle)"/></g>
<rect x="6" y="70" width="138" height="7" rx="2" fill="var(--deco-desk-top)"/>
<rect x="12" y="77" width="126" height="33" rx="2" fill="var(--deco-desk)"/>
<rect x="52" y="84" width="46" height="15" rx="2" fill="var(--color-surface)" stroke="var(--color-brand)" stroke-width="1"/>
<text x="75" y="94.6" text-anchor="middle" font-size="8.5" fill="var(--color-brand)" font-family="Jua, 'Noto Sans KR', sans-serif">여행 상담</text>
<g class="m-bub m-bub-think"><circle cx="124" cy="26" r="13" fill="var(--color-surface)" stroke="var(--color-border-strong)"/><circle cx="111" cy="41" r="3" fill="var(--color-surface)" stroke="var(--color-border-strong)"/>
 <circle class="bd" cx="118" cy="26" r="1.8" fill="var(--color-text-muted)"/><circle class="bd" cx="124" cy="26" r="1.8" fill="var(--color-text-muted)"/><circle class="bd" cx="130" cy="26" r="1.8" fill="var(--color-text-muted)"/></g>
<g class="m-bub m-bub-ask"><circle cx="124" cy="26" r="13" fill="var(--color-surface)" stroke="var(--color-border-strong)"/><path d="M113 36 l-4 7 9 -4z" fill="var(--color-surface)" stroke="var(--color-border-strong)"/>
 <text x="124" y="31.5" text-anchor="middle" font-size="15" font-weight="700" fill="var(--color-brand)" font-family="Jua, sans-serif">?</text></g>
</svg>`;
  const MOOD_TEXT = { idle: "편지를 기다리고 있어요", thinking: "모모가 날아가는 중…", happy: "계획서를 배달했어요!", stamp: "확정 도장 쾅!", asking: "조금만 더 알려 주세요", sorry: "앗, 미안해요" };
  let moodTimer = 0;
  function setMood(m) {
    const d = $("#desk");
    if (!d) return;
    d.dataset.mood = m;
    const line = $("#mood-line");
    if (line) line.textContent = MOOD_TEXT[m] || MOOD_TEXT.idle;
    clearTimeout(moodTimer);
    if (m !== "idle" && m !== "thinking") moodTimer = setTimeout(() => setMood("idle"), 2800);
  }
  const moodFor = (status) => status === "되묻기" ? "asking" : (status === "오류" || status === "거절") ? "sorry" : status === "확정" ? "stamp" : "happy";

  /* ───────── 상태 ───────── */
  const state = {
    sessionId: null, draftVersion: null, status: null, stage: "intake",
    planStatus: null, plan: null, conditions: null, exports: null,
    busy: false, openCond: null, mapDay: "all", mobileMapOpen: false,
    expenses: null, progress: null,
    search: { q: "", results: [], loading: false }, pending: {},
    manual: { open: false, name: "", address: "", lat: null, lng: null, src: null, picking: false, place: null }, cctv: [], routeFit: false,
  };
  const canEdit = () => !READ_ONLY && state.planStatus === "승인 대기";

  /* ───────── 토스트 ───────── */
  let toastTimer = 0;
  function toast(msg, ms = 3200) {
    const t = $("#toast");
    if (!t) return;
    t.innerHTML = Array.isArray(msg) ? msg.map((m) => `<span>${esc(m)}</span>`).join("") : `<span>${esc(msg)}</span>`;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), ms);
  }

  /* ───────── API ───────── */
  async function callApi(body) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 150000);
    try {
      const r = await fetch("/query", {
        method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify(Object.assign({ session_id: state.sessionId, draft_version: state.draftVersion }, body)),
        signal: ctrl.signal,
      });
      let j = null;
      try { j = await r.json(); } catch (e) { j = null; }
      if (!j || typeof j !== "object") j = { answer: "앗, 편지를 전하다가 바람에 날려 버렸어요. 잠시 후 다시 보내 주실래요?", status: "오류" };
      if (!r.ok && !j.status) j.status = "오류";
      return j;
    } catch (e) {
      return { answer: e && e.name === "AbortError" ? "앗, 너무 멀리 날아갔다가 길을 잃었어요. 한 번만 다시 보내 주실래요?" : "앗, 연결이 끊겼어요. 인터넷 연결을 확인하고 다시 보내 주실래요?", status: "오류" };
    } finally { clearTimeout(timer); }
  }

  function setBusy(on, kind) {
    state.busy = on;
    document.body.classList.toggle("is-busy", on);
    ["#cond-panel", "#act-panel"].forEach((s) => {
      const el = $(s);
      if (!el) return;
      el.setAttribute("aria-busy", on ? "true" : "false");
      el.classList.toggle("is-loading", on && kind !== "chat");
    });
    const sendBtn = $("#send");
    if (sendBtn) sendBtn.disabled = on;
    // 대기 중에는 편집 버튼만 막고 브로셔·지도는 그대로 볼 수 있게 (응답이 오면 다시 그리면서 풀림)
    if (on) $$("#brochure .edit button, #brochure .edit select, #brochure .c-add button, #brochure .ps-add button, form[data-form='cost-add'] button, [data-act='cost-del'], [data-act='approve'], [data-act='cond-apply']")
      .forEach((b) => { b.disabled = true; });
  }

  /* 느린 재계획: POST /query/async → GET /jobs/{id} 1.5초 폴링 */
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function jobLine(sec) {
    let el = $("#job-line");
    if (sec == null) { if (el && el.parentNode) el.parentNode.removeChild(el); return; }
    if (!el) {
      el = document.createElement("div");
      el.id = "job-line"; el.className = "job-line"; el.setAttribute("role", "status");
      el.innerHTML = `<span class="job-bar" aria-hidden="true"></span><span class="job-t"></span>`;
    }
    const host = MQ_PC.matches ? $("#side") : $(".m-condbar");
    if (host && el.parentNode !== host) host.insertBefore(el, host.firstChild);
    $(".job-t", el).textContent = `모모가 새 계획을 짜는 중… ${Math.max(0, Math.round(sec))}초`;
  }
  async function callAsync(body) {
    const fail = (msg) => ({ answer: msg || "앗, 계획을 다시 세우다 길을 잃었어요. 한 번만 다시 해 주실래요?", status: "오류" });
    let r, j;
    try {
      r = await fetch("/query/async", { method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify(Object.assign({ session_id: state.sessionId, draft_version: state.draftVersion }, body)) });
      j = await r.json().catch(() => null);
    } catch (e) { return fail("앗, 연결이 끊겼어요. 인터넷 연결을 확인하고 다시 해 주실래요?"); }
    if (r.status === 404 || r.status === 405) return callApi(body); // 비동기 경로가 없는 서버
    if (!j) return fail();
    if (j.session_id) state.sessionId = j.session_id;
    if (!j.job_id) return j.answer ? Object.assign({}, j, { status: r.ok ? j.status : "오류" }) : fail();
    if (r.status === 409) toast(j.answer || "이전 요청을 처리하는 중이에요. 끝나면 바로 보여 드릴게요.");
    const t0 = Date.now();
    let misses = 0;
    jobLine(0);
    for (;;) {
      await sleep(1500);
      let s = null;
      try { const rr = await fetch("/jobs/" + encodeURIComponent(j.job_id)); s = await rr.json(); } catch (e) { s = null; }
      if (!s) { if (++misses > 6) return fail("앗, 진행 상황을 확인하지 못했어요. 잠시 후 '내 여행 계획'에서 다시 열어 주세요."); continue; }
      misses = 0;
      jobLine(s.elapsed_sec != null ? s.elapsed_sec : (Date.now() - t0) / 1000);
      if (s.state === "done") return s.response || fail();
      if (s.state === "error") return s.response ? Object.assign({}, s.response, { status: "오류" }) : fail(s.answer);
      if (Date.now() - t0 > 10 * 60 * 1000) return fail("앗, 너무 오래 걸리고 있어요. 잠시 후 다시 해 주실래요?");
    }
  }

  /** kind: chat | edit | set | cost | approve — edit·set·cost는 브로셔를 제자리에서 갱신 */
  const IN_PLACE = { edit: 1, set: 1, cost: 1 };
  async function send(body, kind, userText) {
    if (state.busy) { toast("모모가 아직 날아가는 중이에요. 잠시만 기다려 주세요."); return; }
    if (DEMO && kind !== "chat") { toast("데모 모드에서는 바뀌지 않아요"); return; }
    if (userText) bubble("me", userText);
    const wait = (kind === "chat" || kind === "approve") ? loadingBubble() : null;
    const useAsync = !DEMO && (body.action === "start" || !!IN_PLACE[kind]);
    setBusy(true, kind);
    setMood("thinking");
    renderStarter();
    let j;
    if (DEMO) {
      await new Promise((r) => setTimeout(r, 900));
      j = body.action === "start" ? await demoPlanResponse(false)
        : { answer: "데모 모드에서는 모모가 실제로 날아가지 않아요. 브로셔와 지도, 버튼 모양을 둘러봐 주세요!", status: "정보", stage: state.stage, quick_replies: ["알아서 짜 줘"] };
    } else if (useAsync) {
      try { j = await callAsync(body); } finally { jobLine(null); }
    } else {
      j = await callApi(body);
    }
    if (wait) wait.stop();
    setBusy(false, kind);
    handleResponse(j, kind);
    if (useAsync && IN_PLACE[kind] && j && j.plan && j.status !== "오류") toast("새 계획으로 바꿨어요");
  }

  async function demoPlanResponse(done) {
    const plan = await fetch("/static/sample_plan.json").then((r) => r.json());
    const answerHtml = await fetch("/static/sample_answer.html").then((r) => (r.ok ? r.text() : "")).catch(() => "");
    return {
      answer: "부모님과 함께하는 순천/구례 여행 계획서를 만들었어요!\n※ 걷기 편한 평지 위주로 골랐고, 계단이 많은 곳은 주의 문구를 달았어요.\n※ 현지 이동은 렌터카로 가정했어요. 오른쪽 '여행 조건'에서 바로 바꿀 수 있어요.\n☁ 날씨: 12월 중순 순천은 낮 7~10℃ 안팎이에요(평년 기준).\n■ 1일차 12월 12일(토)\n12:00–13:00 남녘들밥상\n13:15–15:15 순천만국가정원\n15:40–17:10 순천만습지\n■ 2일차 12월 13일(일)\n10:50–12:20 화엄사\n12:55–13:55 사성암\n여유가 되면 낙안읍성도 들러 보세요.",
      answer_html: answerHtml,
      status: done ? "확정" : "승인 대기", stage: "plan", session_id: "demo", draft_version: "demo-1", plan,
      quick_replies: ["2일차를 더 여유롭게", "낙안읍성도 넣어 줘", "이대로 좋아요"],
      share_url: "#", html_url: "#", docx_url: "#", print_url: "#", ics_url: "#", report_url: "#",
    };
  }

  function handleResponse(j, kind) {
    if (j.session_id) state.sessionId = j.session_id;
    if (j.plan) S.collapsed = true;
    if (kind === "set" && j.status !== "오류") { state.pending = {}; Object.keys(condPlace).forEach((k) => delete condPlace[k]); }
    if (!DEMO && state.sessionId) {
      if (j.plan) Trips.remember(state.sessionId);
      Trips.setCurrent(state.sessionId);
    }
    if (j.draft_version) state.draftVersion = j.draft_version;
    state.status = j.status || null;
    if (j.stage) state.stage = j.stage;
    if (j.status === "승인 대기" || j.status === "확정") state.planStatus = j.status;
    if (j.status === "확정") {
      state.exports = { share_url: j.share_url, html_url: j.html_url, docx_url: j.docx_url, print_url: j.print_url, ics_url: j.ics_url, report_url: j.report_url };
    }
    const changes = Array.isArray(j.changes) ? j.changes.filter(Boolean) : [];
    const b = bubble("momo", j.answer || "", { replies: j.quick_replies || j.chips || [], changes, status: j.status, html: j.answer_html || "", longPlan: !!j.plan && (j.stage === "plan" || j.stage === "refine") });
    if (changes.length) toast(changes, 5200);
    if (j.plan) {
      state.plan = j.plan;
      if (j.plan.trip && j.plan.trip.checklist) state.conditions = j.plan.trip.checklist;
      if (j.plan.trip && j.plan.trip.start_date) document.documentElement.dataset.season = seasonOf(j.plan.trip.start_date);
      const inPlace = !!IN_PLACE[kind];
      renderBrochure({ moveAfter: inPlace ? null : b });
      if (!inPlace && b) requestAnimationFrame(() => b.scrollIntoView({ behavior: "auto", block: "start" }));
    } else if (state.plan) {
      renderBrochure({}); // 상태(확정 등)만 바뀐 경우 편집 버튼 갱신
    }
    if (Array.isArray(j.conditions) && j.conditions.length) state.conditions = j.conditions;
    if (j.progress) state.progress = j.progress;
    else if (j.plan && j.plan.trip && j.plan.trip.progress) state.progress = j.plan.trip.progress;
    else if (j.plan || j.conditions) state.progress = null;
    setMood(moodFor(j.status));
    renderSide();
    renderStarter();
    if (!j.plan && b && !IN_PLACE[kind]) b.scrollIntoView({ behavior: scrollBehavior(), block: "nearest" });
  }

  /* ───────── 대화 ───────── */
  function splitAnswer(text) {
    const lines = String(text || "").split(/\r?\n/);
    const idx = lines.findIndex((l) => /^\s*■/.test(l));
    if (idx < 0) return { head: lines.join("\n").trim(), tail: "" };
    return { head: lines.slice(0, idx).join("\n").trim(), tail: lines.slice(idx).join("\n").trim() };
  }

  /** 서버 answer_html을 한 번 더 거른다: 허용 태그·class만, img는 https만, 그 밖의 속성 제거 */
  const ALLOWED_TAGS = new Set(["DIV", "SECTION", "H3", "H4", "P", "STRONG", "EM", "UL", "OL", "LI", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD", "IMG", "SPAN", "SMALL", "HR", "DETAILS", "SUMMARY", "BR", "B"]);
  function cleanHtml(html) {
    const tpl = document.createElement("template");
    tpl.innerHTML = String(html || "");
    const walk = (node) => {
      Array.from(node.childNodes).forEach((n) => {
        if (n.nodeType === 3) return;
        if (n.nodeType !== 1 || !ALLOWED_TAGS.has(n.tagName)) {
          if (n.nodeType === 1 && !/^(SCRIPT|STYLE|IFRAME|OBJECT|EMBED|TEMPLATE)$/.test(n.tagName)) { walk(n); n.replaceWith(...n.childNodes); }
          else n.remove();
          return;
        }
        Array.from(n.attributes).forEach((a) => {
          const name = a.name.toLowerCase();
          const ok = name === "class" || (n.tagName === "IMG" && (name === "alt" || (name === "src" && /^https:\/\//i.test(a.value)))) || ((n.tagName === "TH" || n.tagName === "TD") && (name === "colspan" || name === "rowspan" || name === "scope"));
          if (!ok) n.removeAttribute(a.name);
        });
        if (n.tagName === "IMG") { if (!n.getAttribute("src")) { n.remove(); return; } n.setAttribute("loading", "lazy"); n.setAttribute("decoding", "async"); if (!n.hasAttribute("alt")) n.setAttribute("alt", ""); }
        if (n.tagName === "TABLE" && !(n.parentNode && n.parentNode.classList && n.parentNode.classList.contains("mm-scroll"))) {
          const w = document.createElement("div"); w.className = "mm-scroll"; n.replaceWith(w); w.appendChild(n);
        }
        walk(n);
      });
    };
    walk(tpl.content);
    const box = document.createElement("div");
    box.appendChild(tpl.content);
    return box.innerHTML;
  }
  function openAnswerWindow(html) {
    const css = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map((l) => `<link rel="stylesheet" href="${esc(l.href)}">`).join("");
    const page = `<!doctype html><html lang="ko" data-brand="${esc(document.documentElement.dataset.brand || "momo")}" data-season="${esc(document.documentElement.dataset.season || "winter")}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>모모의 답변</title>${css}</head>
      <body class="answer-page"><main class="answer-main"><div class="bub rich">${cleanHtml(html)}</div></main></body></html>`;
    const url = URL.createObjectURL(new Blob([page], { type: "text/html;charset=utf-8" }));
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function bubble(who, text, opt = {}) {
    const thread = $("#thread");
    if (!thread) return null;
    $$(".replies", thread).forEach((r) => r.remove()); // 지난 빠른 답장은 치운다
    const wrap = document.createElement("div");
    wrap.className = "msg " + who + (opt.status === "오류" ? " is-error" : "");
    if (who === "me") {
      wrap.innerHTML = `<div class="bub"><p class="txt">${esc(text)}</p></div>`;
    } else {
      const { head, tail } = opt.longPlan ? splitAnswer(text) : { head: String(text || "").trim(), tail: "" };
      const changes = (opt.changes || []).length
        ? `<ul class="changes" aria-label="바뀐 점">${opt.changes.map((c) => `<li><strong>${esc(c)}</strong></li>`).join("")}</ul>` : "";
      const more = tail ? `<details class="long"><summary>글로 된 일정 보기</summary><p class="txt">${richText(tail)}</p></details>` : "";
      const replies = (opt.replies || []).length
        ? `<div class="replies" role="group" aria-label="빠른 답장">${opt.replies.map((r) => `<button type="button" class="chip" data-act="qr" data-q="${esc(r)}">${esc(r)}</button>`).join("")}</div>` : "";
      const rich = opt.html ? cleanHtml(opt.html) : "";
      const popHref = opt.html ? (DEMO ? "#" : `/answer/${encodeURIComponent(state.sessionId || "")}`) : "";
      const pop = popHref && (DEMO || state.sessionId)
        ? `<a class="pop" href="${esc(popHref)}" target="_blank" rel="noopener"${DEMO ? ' data-act="pop-demo"' : ""}>${ico("ico-pop")}<span>새 창으로 보기</span></a>` : "";
      wrap.innerHTML = `<div class="avatar">${bird()}</div><div class="bub-col"><div class="bub${rich ? " rich" : ""}">
        <span class="who">모모${opt.status && opt.status !== "오류" ? ` · <em>${esc(opt.status)}</em>` : ""}${pop}</span>
        ${changes}${rich || `${head ? `<p class="txt">${richText(head)}</p>` : ""}${more}`}</div>${replies}</div>`;
      if (rich) wrap._html = opt.html;
    }
    thread.appendChild(wrap);
    if (who === "me") wrap.scrollIntoView({ behavior: scrollBehavior(), block: "end" });
    return wrap;
  }

  function loadingBubble() {
    const thread = $("#thread");
    const el = document.createElement("div");
    el.className = "msg momo is-loading";
    el.setAttribute("role", "status");
    el.innerHTML = `<div class="avatar flying">${bird()}</div><div class="bub-col"><div class="bub"><p class="txt"><span class="lt">모모가 날아가는 중…</span> <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span></p></div></div>`;
    thread.appendChild(el);
    el.scrollIntoView({ behavior: scrollBehavior(), block: "end" });
    const t1 = setTimeout(() => { const s = $(".lt", el); if (s) s.textContent = "운영시간이랑 휴무일까지 확인하고 있어요…"; }, 12000);
    const t2 = setTimeout(() => { const s = $(".lt", el); if (s) s.textContent = "조금 멀리 날아가고 있어요. 일정을 다 짜려면 30초쯤 걸려요…"; }, 28000);
    return { stop() { clearTimeout(t1); clearTimeout(t2); el.remove(); } };
  }

  /** 새 상담: 맨 위에 여행 시작 카드 */
  function welcome() {
    const thread = $("#thread");
    if (!thread) return;
    let host = $("#starter");
    if (!host) {
      host = document.createElement("section");
      host.id = "starter";
      host.setAttribute("aria-label", "여행 시작 카드");
      thread.insertBefore(host, thread.firstChild);
    }
    renderStarter();
  }

  /* ───────── 여행 시작 카드 (선택형 입력) ───────── */
  const DEFAULT_OPTIONS = {
    regions: [
      { id: "gyeongju", label: "경주", desc: "신라 천년 고도 — 불국사·대릉원·동궁과 월지" },
      { id: "suncheon_gurye", label: "순천/구례", desc: "순천만 갈대숲과 지리산 자락 사찰" },
      { id: "suwon", label: "수원", desc: "화성 성곽길과 행궁, 통닭 골목" },
    ],
    duration_presets: [{ key: "day", label: "당일치기", nights: 0 }, { key: "1n", label: "1박 2일", nights: 1 }, { key: "2n", label: "2박 3일", nights: 2 }, { key: "3n", label: "3박 4일", nights: 3 }, { key: "4n", label: "4박 5일", nights: 4 }, { key: "6n", label: "일주일(6박 7일)", nights: 6 }],
    style_presets: [
      { key: "family", label: "가족 체험", desc: "아이와 체험·넓은 야외", apply: { intensity: "보통", companion_type: "어린이·유아", interests: ["체험", "자연"] } },
      { key: "parents", label: "부모님 효도", desc: "걷기 편한 곳, 여유로운 동선", apply: { intensity: "여유", companion_type: "노약자", interests: ["사찰", "자연"] } },
      { key: "couple", label: "커플 데이트", desc: "카페·야경·사진 명소", apply: { intensity: "보통", companion_type: "보통", interests: ["카페", "사진"] } },
      { key: "food", label: "맛집 투어", desc: "지역 대표 음식 위주", apply: { intensity: "보통", companion_type: "보통", interests: ["맛집"] } },
      { key: "history", label: "역사 탐방", desc: "유적·박물관을 촘촘히", apply: { intensity: "빡빡", companion_type: "보통", interests: ["역사", "사찰"] } },
      { key: "healing", label: "자연 힐링", desc: "숲길·바다·정원", apply: { intensity: "여유", companion_type: "보통", interests: ["자연"] } },
    ],
    companion_choices: [
      { label: "혼자", type: "보통", count: 1 }, { label: "배우자/연인·친구", type: "보통", count: 2 },
      { label: "가족(아이 동반)", type: "어린이·유아", count: 4 }, { label: "부모님 동반", type: "노약자", count: 4 },
    ],
    transport_combos: [
      { label: "자차", long: "자차", local: "자차" }, { label: "KTX·SRT + 렌터카", long: "KTX·SRT", local: "렌터카" },
      { label: "KTX·SRT + 대중교통", long: "KTX·SRT", local: "대중교통" }, { label: "고속버스 + 대중교통", long: "고속·시외버스", local: "대중교통" }, { label: "비행기 + 렌터카", long: "비행기", local: "렌터카" },
    ],
    vehicle_types: ["경차", "소형", "중형", "대형", "SUV", "승합"],
    fuels: ["휘발유", "고급휘발유", "경유", "LPG", "하이브리드", "전기"],
    default_efficiency: {},
    toll_discounts: ["없음", "경차 50%", "친환경차 50%", "장애인 50%", "면제"],
    parking_discounts: ["없음", "경차 50%", "친환경차 50%", "장애인 50%"],
    intensities: [{ value: "빡빡", desc: "하루 4~5곳 · 08~20시" }, { value: "보통", desc: "하루 3~4곳 · 09~18:30" }, { value: "여유", desc: "하루 2~3곳 · 10~17:30" }],
    interests: ["자연", "사찰", "역사", "체험", "카페", "사진", "맛집", "야경"],
    budget_presets: ["30만 원", "50만 원", "100만 원"],
    origins: ["서울", "경기 수원", "대전", "광주", "부산", "대구"],
    max_nights: 8,
  };
  let OPT = DEFAULT_OPTIONS;
  const optLabel = (o) => (o == null ? "" : typeof o === "string" ? o : (o.label || o.value || o.key || ""));
  const REGION_ICON = {
    gyeongju: `<path d="M12 3l7 4H5zM6 7h12M7.5 7v4M16.5 7v4M5 11h14l-2 3H7zM8 14v6M16 14v6M5 20h14" />`,
    suncheon: `<path d="M3 17c2.5-2 4.5-2 7 0s4.5 2 7 0 3-1.5 4-1M3 21c2.5-2 4.5-2 7 0s4.5 2 7 0M7 14V5M7 5c-1 2-1 3 0 4M7 5c1 2 1 3 0 4M12 13V3M12 3c-1 2-1 3 0 4M12 3c1 2 1 3 0 4M17 14V6" />`,
    suwon: `<path d="M3 20h18M4 20V10h16v10M4 10l2-3h12l2 3M9 20v-4a3 3 0 016 0v4M3 10h2M19 10h2M7 7V5M17 7V5M12 7V4" />`,
    other: `<path d="M12 21s7-6.2 7-11.3A7 7 0 005 9.7C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>`,
  };
  const regionIcon = (id) => {
    const k = /gyeongju/.test(id) ? "gyeongju" : /suncheon|gurye/.test(id) ? "suncheon" : /suwon/.test(id) ? "suwon" : "other";
    return `<svg class="r-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${REGION_ICON[k]}</svg>`;
  };

  const S = {
    region: null, from: null, to: null, calBase: null, style: null, comp: null, count: 2, combo: null,
    vehOpen: false, vehicle: { type: "중형", fuel: "휘발유", eff: "", toll: "없음", parking: "없음" },
    moreOpen: false, origin: "", origin_place: null, depart_time: "", end_location: "", end_place: null, arrive_time: "",
    intensity: "보통", budget: null, budgetCustom: "", interests: [], waypoints: "", collapsed: false, wknd: null,
  };
  const S0 = JSON.parse(JSON.stringify(S));

  /* 날짜 도우미 (로컬 날짜 기준) */
  const pad2 = (n) => String(n).padStart(2, "0");
  const isoOf = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const dOf = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
  const addDays = (iso, n) => { const d = dOf(iso); d.setDate(d.getDate() + n); return isoOf(d); };
  const diffDays = (a, b) => Math.round((dOf(b) - dOf(a)) / 86400000);
  const todayIso = () => isoOf(new Date());
  const nightsLabel = (n) => (n <= 0 ? "당일치기" : `${n}박 ${n + 1}일`);
  function rangeText() {
    if (!S.from) return "";
    const to = S.to || S.from;
    const n = diffDays(S.from, to);
    return `${fmtDate(S.from)}${to !== S.from ? ` ~ ${fmtDate(to)}` : ""} · ${nightsLabel(n)}`;
  }
  function nextSaturday(includeToday) {
    const t = new Date(); const dow = t.getDay();
    let add = (6 - dow + 7) % 7;
    if (add === 0 && !includeToday) add = 7;
    t.setDate(t.getDate() + add);
    return isoOf(t);
  }

  async function loadOptions() {
    const url = DEMO ? "/static/options_sample.json" : "/options";
    try {
      const r = await fetch(url);
      if (r.ok) { const j = await r.json(); if (j && Array.isArray(j.regions) && j.regions.length) OPT = Object.assign({}, DEFAULT_OPTIONS, j); }
    } catch (e) { /* 기본 선택지 사용 */ }
    renderStarter();
  }

  function pickChip(act, value, label, on, extra = "") {
    return `<button type="button" class="chip${on ? " on" : ""}" aria-pressed="${on}" data-act="${act}" data-v="${esc(value)}" data-fid="${act}:${esc(value)}"${extra}>${label}</button>`;
  }

  function calendarHtml() {
    const today = todayIso();
    const base = S.calBase || (S.from ? S.from.slice(0, 7) + "-01" : today.slice(0, 7) + "-01");
    const months = window.matchMedia("(min-width: 700px)").matches ? 2 : 1;
    const maxN = Number(OPT.max_nights != null ? OPT.max_nights : 8);
    const out = [];
    for (let k = 0; k < months; k++) {
      const first = dOf(base); first.setMonth(first.getMonth() + k);
      const y = first.getFullYear(), m = first.getMonth();
      const lead = first.getDay();
      const nd = new Date(y, m + 1, 0).getDate();
      const cells = [];
      for (let i = 0; i < lead; i++) cells.push(`<span class="cal-x" aria-hidden="true"></span>`);
      for (let d = 1; d <= nd; d++) {
        const iso = `${y}-${pad2(m + 1)}-${pad2(d)}`;
        const past = iso < today;
        const to = S.to || S.from;
        const isFrom = iso === S.from, isTo = S.to && iso === S.to;
        const inR = S.from && iso > S.from && to && iso < to;
        const tooFar = S.from && !S.to && iso > S.from && diffDays(S.from, iso) > maxN;
        const dow = (lead + d - 1) % 7;
        const cls = ["cal-d", isFrom ? "from" : "", isTo ? "to" : "", inR ? "in" : "", tooFar ? "far" : "", dow === 0 ? "sun" : dow === 6 ? "sat" : "", iso === today ? "today" : "", S.from && S.to && S.from !== S.to ? "range" : ""].filter(Boolean).join(" ");
        const dt = dOf(iso);
        cells.push(`<button type="button" class="${cls}" data-act="cal-day" data-v="${iso}" data-fid="cal:${iso}" ${past ? "disabled" : ""}
          aria-label="${y}년 ${m + 1}월 ${d}일 ${WD[dt.getDay()]}요일${isFrom ? ", 출발일" : ""}${isTo ? ", 마지막 날" : ""}" aria-pressed="${isFrom || !!isTo || !!inR}">${d}</button>`);
      }
      out.push(`<div class="cal-m"><p class="cal-t">${y}년 ${m + 1}월</p><div class="cal-g" role="group" aria-label="${y}년 ${m + 1}월">
        ${"일월화수목금토".split("").map((w, i) => `<span class="cal-w${i === 0 ? " sun" : i === 6 ? " sat" : ""}" aria-hidden="true">${w}</span>`).join("")}${cells.join("")}</div></div>`);
    }
    const canPrev = base > today.slice(0, 7) + "-01";
    return `<div class="cal" data-months="${months}">
      <button type="button" class="cal-nav prev" data-act="cal-nav" data-v="-1" data-fid="cal-prev" aria-label="이전 달" ${canPrev ? "" : "disabled"}>‹</button>
      <div class="cal-ms">${out.join("")}</div>
      <button type="button" class="cal-nav next" data-act="cal-nav" data-v="1" data-fid="cal-next" aria-label="다음 달">›</button></div>`;
  }

  function needsCar() {
    const c = S.combo != null ? OPT.transport_combos[S.combo] : null;
    return !!c && (c.long === "자차" || c.local === "자차" || c.local === "렌터카");
  }
  function effPlaceholder() {
    const t = OPT.default_efficiency && OPT.default_efficiency[S.vehicle.type];
    const v = t && t[S.vehicle.fuel];
    return v != null ? `${v} (기본값)` : "모르면 비워 두세요";
  }

  function starterSummary() {
    const reg = (OPT.regions.find((r) => r.id === S.region) || {}).label;
    const comp = S.comp != null ? OPT.companion_choices[S.comp] : null;
    const combo = S.combo != null ? OPT.transport_combos[S.combo] : null;
    return [reg, rangeText(), comp ? `${optLabel(comp)} ${S.count}명` : "", combo ? optLabel(combo) : ""].filter(Boolean);
  }

  function renderStarter() {
    const host = $("#starter");
    if (!host || READ_ONLY) return;
    const fid = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.fid : null;
    if (S.collapsed) {
      const sum = starterSummary();
      const t = state.plan && state.plan.trip;
      const line = sum.length > 1 ? sum.join(" · ") : t ? [t.title, fmtDate(t.start_date)].filter(Boolean).join(" · ") : "여행 조건";
      host.className = "starter collapsed";
      host.innerHTML = `<div class="st-mini"><span class="st-mini-k">여행 시작 조건</span><span class="st-mini-v">${esc(line)}</span>
        <button type="button" class="btn xs ghost" data-act="st-open" data-fid="st-open">다시 열기</button></div>`;
      return;
    }
    host.className = "starter";
    const ready = !!(S.region && S.from);
    const regions = OPT.regions.map((r) => `<button type="button" class="region${S.region === r.id ? " on" : ""}" aria-pressed="${S.region === r.id}" data-act="st-region" data-v="${esc(r.id)}" data-fid="region:${esc(r.id)}">
      ${regionIcon(r.id)}<span class="r-name">${esc(r.label)}</span><span class="r-desc">${esc(r.desc || "")}</span></button>`).join("");
    const curN = S.from ? diffDays(S.from, S.to || S.from) : null;
    const presets = OPT.duration_presets || [];
    const hasWknd = presets.some((p) => p.weekend);
    const durs = presets.map((p) => p.weekend
      ? pickChip("st-wknd", p.weekend, esc(p.label), S.wknd === p.weekend)
      : pickChip("st-dur", p.nights, esc(p.label), !S.wknd && S.from && curN === Number(p.nights))).join("")
      + (hasWknd ? "" : pickChip("st-wknd", "this", "이번 주말", S.wknd === "this") + pickChip("st-wknd", "next", "다음 주말", S.wknd === "next"));
    const unit = (OPT.fuel_units && OPT.fuel_units[S.vehicle.fuel]) || "L";
    const styles = (OPT.style_presets || []).map((p) => `<button type="button" class="style-c${S.style === p.key ? " on" : ""}" aria-pressed="${S.style === p.key}" data-act="st-style" data-v="${esc(p.key)}" data-fid="style:${esc(p.key)}">
      <b>${esc(p.label)}</b>${p.desc ? `<span>${esc(p.desc)}</span>` : ""}</button>`).join("");
    const comps = OPT.companion_choices.map((c, i) => pickChip("st-comp", i, esc(optLabel(c)), S.comp === i)).join("");
    const combos = OPT.transport_combos.map((c, i) => pickChip("st-combo", i, esc(optLabel(c)), S.combo === i)).join("");
    const V = S.vehicle;
    const sel = (name, list, cur) => `<select class="sel" data-st="${name}" id="st-${name}">${list.map((o) => `<option${optLabel(o) === cur ? " selected" : ""}>${esc(optLabel(o))}</option>`).join("")}</select>`;
    const vehicle = needsCar() ? `<div class="st-sub">
        <button type="button" class="st-fold" data-act="st-veh" data-fid="st-veh" aria-expanded="${S.vehOpen}" aria-controls="st-veh-b">연료비를 정확히 계산할까요? <span class="muted">차량 정보 (선택)</span>${ico("ico-chev", "ico chev")}</button>
        <div id="st-veh-b" class="st-fold-b"${S.vehOpen ? "" : " hidden"}>
          <div class="st-field"><span class="st-l">차종</span><div class="opts">${OPT.vehicle_types.map((o) => pickChip("st-vtype", optLabel(o), esc(optLabel(o)), V.type === optLabel(o))).join("")}</div></div>
          <div class="st-field"><span class="st-l">연료</span><div class="opts">${OPT.fuels.map((o) => pickChip("st-vfuel", optLabel(o), esc(optLabel(o)), V.fuel === optLabel(o))).join("")}</div></div>
          <div class="st-grid3">
            <label class="st-in"><span>${unit === "L" ? "연비 (km/L)" : `전비 (km/${esc(unit)})`}</span><input type="number" inputmode="decimal" min="1" max="60" step="0.1" data-st="eff" value="${esc(V.eff)}" placeholder="${esc(effPlaceholder())}"></label>
            <label class="st-in"><span>통행료 할인</span>${sel("toll", OPT.toll_discounts, V.toll)}</label>
            <label class="st-in"><span>주차 할인</span>${sel("parking", OPT.parking_discounts, V.parking)}</label>
          </div></div></div>` : "";
    const more = `<div class="st-sub">
        <button type="button" class="st-fold" data-act="st-more" data-fid="st-more" aria-expanded="${S.moreOpen}" aria-controls="st-more-b">출발·도착 정하기 <span class="muted">선택 · 시간을 꽉 채워 쓰도록 첫날·마지막 날을 맞춰요</span>${ico("ico-chev", "ico chev")}</button>
        <div id="st-more-b" class="st-fold-b"${S.moreOpen ? "" : " hidden"}>
          <div class="st-grid2">
            <label class="st-in"><span>출발지 (시·군·구)</span><input type="text" data-st="origin" value="${esc(S.origin)}" placeholder="예) 서울 강남구, 판교동" maxlength="30" autocomplete="off"${geoAttrs("origin")}></label>
            <div class="st-in"><span>출발 시각</span>${tpTrigger("st-depart", "depart", S.depart_time)}</div>
          </div>
          <div class="opts">${(OPT.origins || DEFAULT_OPTIONS.origins).map((o) => pickChip("st-origin", optLabel(o), esc(optLabel(o)), S.origin === optLabel(o), ' data-size="sm"')).join("")}</div>
          <div class="st-grid2">
            <label class="st-in"><span>여행 후 도착지</span><input type="text" data-st="end_location" value="${esc(S.end_location)}" placeholder="${esc(S.origin ? S.origin + " (출발지와 같아요)" : "출발지와 같아요")}" maxlength="30" autocomplete="off"${geoAttrs("end")}></label>
            <div class="st-in"><span>도착 희망 시각</span>${tpTrigger("st-arrive", "arrive", S.arrive_time)}</div>
          </div></div></div>`;
    const ints = (OPT.intensities || []).map((o) => { const v = optLabel(o); return `<button type="button" class="int-c${S.intensity === v ? " on" : ""}" aria-pressed="${S.intensity === v}" data-act="st-int" data-v="${esc(v)}" data-fid="int:${esc(v)}"><b>${esc(v)}</b><span>${esc(o.desc || "")}</span></button>`; }).join("");
    const budgets = (OPT.budget_presets || []).map((b) => pickChip("st-budget", optLabel(b), esc(optLabel(b)), S.budget === optLabel(b))).join("") + pickChip("st-budget", "custom", "직접 입력", S.budget === "custom");
    const interests = (OPT.interests || []).map((o) => pickChip("st-interest", optLabel(o), esc(optLabel(o)), S.interests.indexOf(optLabel(o)) >= 0)).join("");
    const sum = starterSummary();

    host.innerHTML = `<div class="st-head">
        <div class="hello-art">${bird("bird big")}</div>
        <div><p class="eyebrow">새 여행 상담 · 고르기만 하면 돼요</p>
        <h2 id="st-h" class="display">어디로 여행 편지를 부칠까요?</h2>
        <p class="muted">여행지와 날짜만 고르면 바로 시작할 수 있어요. 나머지는 모모가 알맞게 가정하고, 나중에 오른쪽에서 바꿀 수 있어요.</p></div>
        ${state.plan ? `<button type="button" class="x" data-act="st-close" aria-label="시작 카드 접기">×</button>` : ""}
      </div>
      <ol class="st-steps">
        <li class="st-step"><h3><span class="st-n">1</span>어디로 갈까요? <em class="req">필수</em></h3><div class="regions">${regions}</div></li>
        <li class="st-step"><h3><span class="st-n">2</span>언제, 며칠 동안? <em class="req">필수</em></h3>
          <div class="opts">${durs}</div>
          ${calendarHtml()}
          <p class="range-t${S.from ? "" : " muted"}" aria-live="polite">${S.from ? esc(rangeText()) + (S.to ? "" : " — 마지막 날을 눌러 주세요 (당일치기면 그대로 두세요)") : `출발일을 누르고, 마지막 날을 눌러 주세요. 최대 ${nightsLabel(Number(OPT.max_nights != null ? OPT.max_nights : 8))}까지 돼요.`}</p></li>
        ${styles ? `<li class="st-step"><h3><span class="st-n">3</span>어떤 여행인가요? <em>선택</em></h3><p class="st-hint">고르면 아래 강도·동반자·관심사를 알맞게 채워 드려요. 다시 바꿔도 돼요.</p><div class="styles">${styles}</div></li>` : ""}
        <li class="st-step"><h3><span class="st-n">${styles ? 4 : 3}</span>누구와 가요?</h3><div class="opts">${comps}</div>
          <div class="stepper" role="group" aria-label="인원"><span class="st-l">인원</span>
            <button type="button" class="step-b" data-act="st-count" data-v="-1" data-fid="cnt-" aria-label="한 명 줄이기" ${S.count <= 1 ? "disabled" : ""}>−</button>
            <output aria-live="polite">${S.count}명</output>
            <button type="button" class="step-b" data-act="st-count" data-v="1" data-fid="cnt+" aria-label="한 명 늘리기" ${S.count >= 20 ? "disabled" : ""}>+</button></div></li>
        <li class="st-step"><h3><span class="st-n">${styles ? 5 : 4}</span>어떻게 이동해요?</h3><div class="opts">${combos}</div>${vehicle}${more}</li>
        <li class="st-step"><h3><span class="st-n">${styles ? 6 : 5}</span>조금 더 알려 주기 <em>선택</em></h3>
          <div class="st-field"><span class="st-l">여행 강도</span><div class="ints">${ints}</div></div>
          <div class="st-field"><span class="st-l">최대 예산</span><div class="opts">${budgets}</div>
            ${S.budget === "custom" ? `<label class="st-in inline"><span class="sr">예산 직접 입력</span><input type="text" data-st="budgetCustom" value="${esc(S.budgetCustom)}" placeholder="예) 70만 원" maxlength="20" autocomplete="off"></label>` : ""}</div>
          <div class="st-field"><span class="st-l">관심사 <small>여러 개</small></span><div class="opts">${interests}</div></div>
          <label class="st-in"><span>꼭 가고 싶은 곳 <small class="muted">쉼표로 구분</small></span><input type="text" data-st="waypoints" value="${esc(S.waypoints)}" placeholder="예) 사성암, 낙안읍성" maxlength="80" autocomplete="off"></label>
        </li>
      </ol>
      <div class="st-go">
        <p class="st-go-sum">${sum.length ? sum.map((x) => `<span>${esc(x)}</span>`).join("") : `<span class="muted">여행지와 날짜를 골라 주세요</span>`}</p>
        <button type="button" class="btn primary" data-act="st-submit" data-fid="st-submit" ${ready && !state.busy ? "" : "disabled"}>모모에게 계획 부탁하기</button>
      </div>
      <p class="st-or">또는 아래 입력창에 편하게 말로 해도 돼요</p>`;
    if (fid) { const el = host.querySelector(`[data-fid="${cssEsc(fid)}"]`); if (el && !el.disabled) el.focus({ preventScroll: true }); }
  }

  function starterClick(t, act) {
    const v = t.dataset.v;
    const maxN = Number(OPT.max_nights != null ? OPT.max_nights : 8);
    switch (act) {
      case "st-region": S.region = S.region === v ? null : v; break;
      case "cal-day": {
        S.wknd = null;
        if (!S.from || S.to) { S.from = v; S.to = null; }
        else if (v < S.from) { S.from = v; }
        else if (diffDays(S.from, v) > maxN) { toast(`최대 ${nightsLabel(maxN)}까지 고를 수 있어요`); return; }
        else S.to = v;
        break;
      }
      case "cal-nav": {
        const b = dOf(S.calBase || (S.from ? S.from.slice(0, 7) + "-01" : todayIso().slice(0, 7) + "-01"));
        b.setMonth(b.getMonth() + Number(v));
        S.calBase = isoOf(b);
        break;
      }
      case "st-dur": {
        const n = Number(v);
        if (!S.from) S.from = nextSaturday(false);
        S.wknd = null;
        S.to = addDays(S.from, n);
        S.calBase = null;
        break;
      }
      case "st-wknd": {
        const t0 = new Date();
        if (v === "this" && t0.getDay() === 0) { S.from = todayIso(); S.to = todayIso(); }
        else { S.from = v === "this" ? nextSaturday(true) : addDays(nextSaturday(true), 7); S.to = addDays(S.from, 1); }
        S.wknd = v;
        S.calBase = null;
        break;
      }
      case "st-style": {
        if (S.style === v) { S.style = null; break; }
        S.style = v;
        const p = (OPT.style_presets || []).find((x) => x.key === v);
        const a = (p && p.apply) || {};
        if (a.intensity) S.intensity = a.intensity;
        if (a.companion_type) {
          const i = OPT.companion_choices.findIndex((c) => c.type === a.companion_type && c.count !== 1);
          const j = i >= 0 ? i : OPT.companion_choices.findIndex((c) => c.type === a.companion_type);
          if (j >= 0) { S.comp = j; S.count = Number(OPT.companion_choices[j].count) || S.count; }
        }
        if (Array.isArray(a.interests)) S.interests = a.interests.slice();
        break;
      }
      case "st-comp": { const i = Number(v); S.comp = S.comp === i ? null : i; if (S.comp != null) S.count = Number(OPT.companion_choices[i].count) || S.count; break; }
      case "st-count": S.count = Math.max(1, Math.min(20, S.count + Number(v))); break;
      case "st-combo": { const i = Number(v); S.combo = S.combo === i ? null : i; break; }
      case "st-veh": S.vehOpen = !S.vehOpen; break;
      case "st-vtype": S.vehicle.type = v; break;
      case "st-vfuel": S.vehicle.fuel = v; break;
      case "st-more": S.moreOpen = !S.moreOpen; break;
      case "st-origin": S.origin = v; S.origin_place = null; break;
      case "st-int": S.intensity = v; break;
      case "st-budget": S.budget = S.budget === v ? null : v; break;
      case "st-interest": { const k = S.interests.indexOf(v); if (k >= 0) S.interests.splice(k, 1); else S.interests.push(v); break; }
      case "st-open": S.collapsed = false; renderStarter(); { const h = $("#starter"); if (h) h.scrollIntoView({ behavior: scrollBehavior(), block: "start" }); } return;
      case "st-close": S.collapsed = true; break;
      case "st-submit": submitStarter(); return;
    }
    renderStarter();
  }

  function starterInput(e) {
    const k = e.target.dataset.st;
    if (!k) return;
    const v = e.target.value;
    if (k === "eff" || k === "toll" || k === "parking") S.vehicle[k] = v;
    else S[k] = v;
    if (k === "origin") { const end = $('[data-st="end_location"]'); if (end) end.placeholder = v ? `${v} (출발지와 같아요)` : "출발지와 같아요"; }
  }

  function submitStarter() {
    if (!S.region || !S.from) { toast("여행지와 날짜를 먼저 골라 주세요"); return; }
    const comp = S.comp != null ? OPT.companion_choices[S.comp] : null;
    const combo = S.combo != null ? OPT.transport_combos[S.combo] : null;
    const style = (OPT.style_presets || []).find((p) => p.key === S.style);
    const eff = parseFloat(S.vehicle.eff);
    const start = {
      region: S.region, start_date: S.from, end_date: S.to || S.from,
      origin: S.origin.trim() || null, origin_place: S.origin_place || null, depart_time: S.depart_time || null,
      end_place: S.end_place || null,
      end_location: S.end_location.trim() || null, arrive_time: S.arrive_time || null,
      long_transport: combo ? combo.long : null, local_transport: combo ? combo.local : null,
      vehicle: needsCar() && S.vehOpen ? { type: S.vehicle.type, fuel: S.vehicle.fuel, efficiency_kmpl: isFinite(eff) && eff > 0 ? eff : null, toll_discount: S.vehicle.toll, parking_discount: S.vehicle.parking } : null,
      intensity: S.intensity || null,
      companions: comp ? { type: comp.type, count: S.count, note: optLabel(comp) } : { type: "보통", count: S.count, note: "" },
      budget: S.budget === "custom" ? (S.budgetCustom.trim() || null) : S.budget,
      interests: S.interests.slice(), preset: style ? style.label : null,
      waypoints: S.waypoints.split(/[,，、]/).map((x) => x.trim()).filter(Boolean),
    };
    const parts = [...starterSummary(), start.intensity ? `강도 ${start.intensity}` : "", start.budget ? `예산 ${start.budget}` : "", start.waypoints.length ? `꼭 갈 곳 ${start.waypoints.join(", ")}` : ""].filter(Boolean);
    const question = `[여행 시작] ${parts.join(" · ")}`;
    send({ question, action: "start", start }, "chat", parts.join(" · "));
  }

  function resetStarter() { Object.assign(S, JSON.parse(JSON.stringify(S0))); }

  /* ───────── 출발지·도착지 자동완성 (GET /geo/search) ───────── */
  const GEO_KEYS = ["origin", "arrival", "end_location"];
  const DEMO_GEO = ["서울 강남구", "서울 종로구", "서울 송파구", "경기 성남시 분당구 판교동", "경기 수원시 팔달구", "대전 유성구", "광주 서구", "부산 해운대구", "대구 중구"];
  function geoAttrs(target, key) {
    return ` data-geo="${target}"${key ? ` data-key="${esc(key)}"` : ""} role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="geo-list"`;
  }
  const Geo = { timer: 0, seq: 0, input: null, items: [], active: -1 };
  function geoClearPlace(inp) {
    const t = inp.dataset.geo;
    if (t === "origin") S.origin_place = null;
    else if (t === "end") S.end_place = null;
    else if (t === "manual" && state.manual.src === "geo") Object.assign(state.manual, { lat: null, lng: null, src: null, place: null });
    else if (t === "cond") delete condPlace[inp.dataset.key];
  }
  function geoClose() {
    const ul = $("#geo-list");
    if (ul) ul.remove();
    if (Geo.input) { Geo.input.setAttribute("aria-expanded", "false"); Geo.input.removeAttribute("aria-activedescendant"); }
    Geo.items = []; Geo.active = -1;
  }
  function geoOnInput(inp) {
    geoClearPlace(inp);
    Geo.input = inp;
    clearTimeout(Geo.timer);
    const q = inp.value.trim();
    if (q.length < 2) { geoClose(); return; }
    Geo.timer = setTimeout(async () => {
      const seq = ++Geo.seq;
      let items = [];
      try {
        if (DEMO) items = DEMO_GEO.filter((x) => x.indexOf(q) >= 0 || x.replace(/\s/g, "").indexOf(q.replace(/\s/g, "")) >= 0).map((label) => ({ label, sido: label.split(" ")[0], source: "OpenStreetMap" }));
        else { const r = await fetch("/geo/search?q=" + encodeURIComponent(q)); const j = await r.json(); items = (j && j.results) || []; }
      } catch (e) { items = []; }
      if (seq !== Geo.seq || Geo.input !== inp || document.activeElement !== inp) return;
      Geo.items = items.slice(0, 6); Geo.active = -1;
      geoRender();
    }, 300);
  }
  function geoRender() {
    const inp = Geo.input;
    let ul = $("#geo-list");
    if (!Geo.items.length) { if (ul) ul.remove(); inp.setAttribute("aria-expanded", "false"); return; }
    if (!ul || ul.previousElementSibling !== inp) { if (ul) ul.remove(); ul = document.createElement("ul"); ul.id = "geo-list"; ul.className = "geo-list"; ul.setAttribute("role", "listbox"); ul.setAttribute("aria-label", "추천 장소"); inp.after(ul); }
    ul.innerHTML = Geo.items.map((it, i) => `<li id="geo-o-${i}" role="option" aria-selected="${i === Geo.active}" class="${i === Geo.active ? "on" : ""}" data-act="geo-pick" data-i="${i}">${ico("ico-pin")}<span>${esc(it.label)}</span></li>`).join("")
      + `<li class="geo-src" aria-hidden="true">지도 정보: OpenStreetMap</li>`;
    inp.setAttribute("aria-expanded", "true");
    if (Geo.active >= 0) inp.setAttribute("aria-activedescendant", "geo-o-" + Geo.active); else inp.removeAttribute("aria-activedescendant");
  }
  function pickGeo(el) {
    const it = Geo.items[Number(el.dataset.i)];
    const inp = Geo.input;
    if (!it || !inp) return;
    inp.value = it.label;
    const t = inp.dataset.geo;
    if (t === "origin") { S.origin = it.label; S.origin_place = it; const end = $('[data-st="end_location"]'); if (end) end.placeholder = `${it.label} (출발지와 같아요)`; }
    else if (t === "end") { S.end_location = it.label; S.end_place = it; }
    else if (t === "manual") { Object.assign(state.manual, { address: it.label, place: it, lat: it.lat != null ? it.lat : null, lng: it.lng != null ? it.lng : null, src: it.lat != null ? "geo" : null }); geoClose(); paintManual(); return; }
    else if (t === "cond") { condPlace[inp.dataset.key] = it; if (inp.dataset.key === ARR.key) ARR.label = it.label; }
    geoClose();
    inp.focus();
  }
  function geoKeydown(e) {
    const inp = e.target;
    if (!inp.dataset || !inp.dataset.geo || !Geo.items.length || Geo.input !== inp) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = Geo.items.length;
      Geo.active = e.key === "ArrowDown" ? (Geo.active + 1) % n : (Geo.active - 1 + n) % n;
      geoRender();
    } else if (e.key === "Enter" && Geo.active >= 0) {
      e.preventDefault();
      pickGeo({ dataset: { i: Geo.active } });
    } else if (e.key === "Escape") { geoClose(); }
  }

  /* ───────── 여행 조건(신호등) ───────── */
  function currentConds() {
    return (state.conditions && state.conditions.length) ? state.conditions : PLACEHOLDER_CONDS;
  }
  function condCounts(conds) {
    const c = { confirmed: 0, assumed: 0, missing: 0, optional: 0 };
    conds.forEach((x) => { if (c[x.status] != null) c[x.status]++; });
    return c;
  }
  function summaryHtml(c) {
    return ["confirmed", "assumed", "missing", "optional"].filter((k) => c[k] || k !== "optional")
      .map((k) => `<span class="sum-i"><i class="dot ${STATUS[k].c}"></i>${STATUS[k].t} ${c[k]}</span>`).join("");
  }

  function renderConditions() {
    const panel = $("#cond-panel");
    if (!panel) return;
    if (READ_ONLY && !(state.conditions && state.conditions.length)) { // 옛 공유 데이터: 조건표 없음
      panel.hidden = true;
      const bar = $(".m-condbar"); if (bar) bar.hidden = true;
      return;
    }
    const conds = currentConds();
    const counts = condCounts(conds);
    const editable = !READ_ONLY;
    // 압축형: 2열 알약. 열린 알약 바로 다음 줄(전체 폭)에 편집기
    const rows = conds.map((c) => {
      const st = STATUS[c.status] || STATUS.missing;
      const val = c.value ? esc(c.value) : `<span class="ph">${c.status === "optional" ? "없음" : "미정"}</span>`;
      const inner = `<i class="dot ${st.c}" aria-hidden="true"></i><span class="lbl">${esc(c.label.replace(/\s*\(선택\)/, ""))}</span><span class="val">${val}</span>`;
      const aria = `${esc(c.label)}: ${esc(c.value || "미정")} (${st.t})`;
      if (!editable) return `<li class="pill s-${st.c}" title="${aria}"><div class="pill-b ro" aria-label="${aria}">${inner}</div></li>`;
      const open = state.openCond === c.key;
      const pd = state.pending[c.key];
      if (pd) {
        return `<li class="pill pend${open ? " open" : ""}"><button type="button" class="pill-b" data-act="cond" data-key="${esc(c.key)}" aria-expanded="${open}" aria-controls="ed-${esc(c.key)}"
          aria-label="${esc(c.label)}: ${esc(pendingShow(c.key, pd.value))}로 변경 예정 (지금 ${esc(c.value || "미정")}) — 눌러서 다시 고르기" title="지금: ${esc(c.value || "미정")}">
          <i class="dot pend" aria-hidden="true"></i><span class="lbl">${esc(c.label.replace(/\s*\(선택\)/, ""))}</span><span class="val">${esc(pendingShow(c.key, pd.value))}</span></button>
          <button type="button" class="pill-x" data-act="cond-revert" data-key="${esc(c.key)}" aria-label="${esc(c.label)} 변경 되돌리기">×</button></li>${open ? `<li class="pill-ed">${condEditor(c)}</li>` : ""}`;
      }
      return `<li class="pill s-${st.c}${open ? " open" : ""}"><button type="button" class="pill-b" data-act="cond" data-key="${esc(c.key)}" aria-expanded="${open}" aria-controls="ed-${esc(c.key)}"
        aria-label="${aria} — 눌러서 바꾸기" title="${esc(c.value || "")}">${inner}</button></li>${open ? `<li class="pill-ed">${condEditor(c)}</li>` : ""}`;
    }).join("");
    if (!$("#cond-body", panel)) {
      panel.innerHTML = `<div class="panel-h"><h2 class="ph-title">여행 조건</h2><p class="sum" id="cond-sum" aria-label="조건 요약"></p></div>
        ${progressHtml("prog")}<div id="cond-body"></div><div id="pend-wrap"></div><div class="bar-loading" aria-hidden="true"></div>`;
    }
    const np = pendingCount();
    $("#cond-sum", panel).innerHTML = summaryHtml(counts);
    $("#cond-body", panel).innerHTML = `<ul class="cond-grid">${rows}</ul>`;
    $("#pend-wrap", panel).innerHTML = pendingBar();
    const ms = $("#m-cond-sum");
    if (ms) ms.innerHTML = summaryHtml(counts) + (np ? `<span class="sum-i pend-n">변경 예정 ${np}</span>` : "");
    updateProgress();
  }

  /* 확정 진행률: 서버 progress 우선, 없으면 체크리스트로 계산 */
  function getProgress() {
    if (state.progress && state.progress.percent != null) return state.progress;
    const conds = currentConds().filter((c) => c.status !== "optional");
    const total = conds.length || 1;
    const confirmed = conds.filter((c) => c.status === "confirmed").length;
    const percent = Math.round((confirmed / total) * 100);
    return { percent, confirmed, total, assumed: conds.filter((c) => c.status === "assumed").map((c) => c.label),
      missing: conds.filter((c) => c.status === "missing").map((c) => c.label) };
  }
  function progressHtml(id) {
    return `<div class="prog" id="${id}"><div class="prog-top"><span class="prog-l"></span><span class="prog-n"></span></div>
      <div class="prog-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-label="여행 조건 확정 진행률"><span class="prog-fill"></span></div>
      <p class="prog-note"></p></div>`;
  }
  function updateProgress() {
    const pg = getProgress();
    const pct = Math.max(0, Math.min(100, Number(pg.percent) || 0));
    const done = pct >= 100;
    const na = (pg.assumed || []).length, nm = (pg.missing || []).length;
    const rest = [na ? `가정 ${na}` : "", nm ? `미정 ${nm}` : ""].filter(Boolean).join(" · ");
    const note = done ? "모든 조건이 확정됐어요." : (rest || "조건을 알려 주시면 더 정확해져요") + (READ_ONLY ? "" : " — 눌러서 확정하거나 그대로 진행할 수 있어요");
    $$(".prog").forEach((el) => {
      el.classList.toggle("done", done);
      $(".prog-l", el).textContent = done ? "100% 확정 완료" : `${pct}% 진행 중`;
      $(".prog-n", el).textContent = pg.total ? `확정 ${pg.confirmed != null ? pg.confirmed : "-"}/${pg.total}` : "";
      const tr = $(".prog-track", el);
      tr.setAttribute("aria-valuenow", String(pct));
      tr.setAttribute("aria-valuetext", done ? "100% 확정 완료" : `${pct}% 진행 중`);
      $(".prog-fill", el).style.width = pct + "%";
      const n = $(".prog-note", el);
      if (n) n.textContent = note;
    });
  }

  function condEditor(c) {
    const id = `ed-${esc(c.key)}`;
    const hint = c.hint ? `<p class="hint">${esc(c.hint)}</p>` : "";
    let body = "";
    const pend = state.pending[c.key];
    if (c.key === "arrival") {
      // 도착: 장소(자동완성)와 시각(TimePicker)을 따로
      if (ARR.key !== c.key) arrInit(c);
      body = `<form class="ed-form arr-form" data-form="arrival" data-key="${esc(c.key)}">
        <label class="st-in"><span>여행 후 도착지</span><input type="text" name="place" value="${esc(ARR.label)}" placeholder="출발지와 같아요" maxlength="40" autocomplete="off"${geoAttrs("cond", c.key)}></label>
        <div class="st-in"><span>도착 희망 시각</span>${tpTrigger("arr-time", "arrive", ARR.time)}</div>
        <button class="btn sm primary">변경 예정에 넣기</button></form>`;
    } else if (c.input === "time" || c.key === "depart_time") {
      const kind = /arriv|end|도착/.test(c.key + c.label) ? "arrive" : "depart";
      body = `<div class="ed-time">${tpTrigger("cond:" + c.key, kind, pend ? hhmm(pend.value) : condCurrent(c))}</div>`;
    } else if (c.input === "date") {
      const val = pend ? pend.value : (c.iso || "");
      body = `<form class="ed-form" data-form="set" data-key="${esc(c.key)}"><label class="sr" for="in-${id}">${esc(c.label)}</label>
        <input id="in-${id}" type="date" name="v" value="${esc(val)}" required><button class="btn sm primary">바꾸기</button></form>`;
    } else if (c.input === "text" || !(c.options || []).length) {
      body = `<form class="ed-form" data-form="set" data-key="${esc(c.key)}"><label class="sr" for="in-${id}">${esc(c.label)}</label>
        <input id="in-${id}" type="text" name="v" value="${esc(pend ? pend.value : (c.status === "confirmed" ? c.value || "" : ""))}" placeholder="${esc(c.value || "직접 적어 주세요")}" autocomplete="off" maxlength="60" required${GEO_KEYS.indexOf(c.key) >= 0 ? geoAttrs("cond", c.key) : ""}>
        <button class="btn sm primary">바꾸기</button></form>
        ${(c.options || []).length ? `<div class="opts" role="group" aria-label="추천 값">${c.options.map((o) => `<button type="button" class="chip sm" data-act="cond-sugg" data-key="${esc(c.key)}" data-v="${esc(o)}">${esc(o)}</button>`).join("")}</div>` : ""}`;
    } else {
      body = `<div class="opts" role="group" aria-label="${esc(c.label)} 고르기">${(c.options || []).map((o) => {
        const on = c.value && String(c.value).indexOf(o) === 0;
        return `<button type="button" class="chip${on ? " on" : ""}" aria-pressed="${on}" data-act="cond-set" data-key="${esc(c.key)}" data-v="${esc(o)}">${esc(o)}</button>`;
      }).join("")}</div>`;
    }
    return `<div class="cond-ed" id="${id}">${hint}${body}</div>`;
  }

  /* ───────── 시각 고르기 (공용 TimePicker) ─────────
   * 트리거 버튼 → 팝오버(모바일은 바텀시트): 빠른 선택 · 시 그리드 · 분 칩 · 선택 안 함 · 확인
   * 값은 항상 "HH:MM" 또는 "" (선택 안 함). 다시 열면 현재 값(변경 예정 포함)이 선택돼 있다.
   */
  const TP_CFG = {
    depart: { quick: ["06:00", "07:00", "08:00", "09:00", "10:00"], from: 5, to: 12, word: "출발", title: "출발 시각", ph: "출발 시각 선택(선택)" },
    arrive: { quick: ["18:00", "19:00", "20:00", "21:00", "22:00"], from: 14, to: 23, word: "도착", title: "도착 희망 시각", ph: "도착 시각 선택(선택)" },
  };
  const TP_MIN = ["00", "10", "20", "30", "40", "50"];
  const TP = { target: null, kind: "depart", h: null, m: null, trigger: null };
  const hhmm = (v) => { const m = /(\d{1,2}):(\d{2})/.exec(String(v || "")); return m ? `${pad2(Number(m[1]))}:${m[2]}` : ""; };
  function tpTrigger(target, kind, value) {
    const cfg = TP_CFG[kind];
    return `<button type="button" class="tp-trigger${value ? " has" : ""}" data-act="tp-open" data-tp="${esc(target)}" data-kind="${kind}" data-fid="tp:${esc(target)}"
      aria-haspopup="dialog" aria-expanded="false" aria-label="${esc(cfg.title)}: ${value ? esc(value) : "선택 안 함"} — 눌러서 고르기">
      ${ico("ico-clock")}<span class="tp-v">${value ? `<b>${esc(value)}</b> ${cfg.word}` : esc(cfg.ph)}</span>${ico("ico-chev", "ico chev")}</button>`;
  }
  function condCurrent(c) {
    if (!c) return "";
    if (c.input === "date") return c.iso || "";
    if (c.input === "time") return c.iso || hhmm(c.value);
    return c.value || "";
  }
  function tpGet(target) {
    if (target === "st-depart") return S.depart_time || "";
    if (target === "st-arrive") return S.arrive_time || "";
    if (target === "arr-time") return ARR.time || "";
    if (target.indexOf("cond:") === 0) {
      const key = target.slice(5);
      if (state.pending[key]) return hhmm(state.pending[key].value);
      return condCurrent(currentConds().find((x) => x.key === key));
    }
    return "";
  }
  function tpSet(target, v) {
    if (target === "st-depart") { S.depart_time = v; renderStarter(); }
    else if (target === "st-arrive") { S.arrive_time = v; renderStarter(); }
    else if (target === "arr-time") { ARR.time = v; renderConditions(); const tr = $('[data-tp="arr-time"]'); if (tr) tr.focus(); }
    else if (target.indexOf("cond:") === 0) sendSet(target.slice(5), v);
  }
  function tpPop() {
    let el = $("#tp-pop");
    if (!el) {
      el = document.createElement("div");
      el.id = "tp-pop"; el.className = "tp-pop"; el.setAttribute("role", "dialog"); el.hidden = true;
      const back = document.createElement("div"); back.id = "tp-back"; back.className = "tp-back"; back.hidden = true; back.dataset.act = "tp-close";
      document.body.append(back, el);
      el.addEventListener("keydown", tpKeydown);
    }
    return el;
  }
  function tpOpen(btn) {
    if (TP.trigger && TP.trigger !== btn) tpClose(false);
    TP.target = btn.dataset.tp; TP.kind = btn.dataset.kind === "arrive" ? "arrive" : "depart"; TP.trigger = btn;
    const v = tpGet(TP.target);
    TP.h = v ? v.slice(0, 2) : null; TP.m = v ? v.slice(3, 5) : null;
    const el = tpPop();
    el.setAttribute("aria-label", TP_CFG[TP.kind].title + " 고르기");
    el.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    tpRender(true);
    tpPlace();
  }
  function tpRender(focusFirst) {
    const el = $("#tp-pop");
    if (!el || el.hidden) return;
    const cfg = TP_CFG[TP.kind];
    const fa = document.activeElement && el.contains(document.activeElement) ? document.activeElement.dataset.k : null;
    const hours = []; for (let h = cfg.from; h <= cfg.to; h++) hours.push(pad2(h));
    if (TP.h && hours.indexOf(TP.h) < 0) hours.push(TP.h), hours.sort();
    const mins = TP_MIN.slice(); if (TP.m && mins.indexOf(TP.m) < 0) mins.push(TP.m), mins.sort();
    const cur = TP.h ? `${TP.h}:${TP.m || "00"}` : "";
    const b = (act, v, label, on, extra = "") => `<button type="button" class="tp-b${on ? " on" : ""}" data-act="${act}" data-v="${v}" data-k="${act}:${v}" aria-pressed="${on}"${extra}>${label}</button>`;
    el.innerHTML = `<div class="tp-head"><p class="tp-title">${esc(cfg.title)}</p><p class="tp-now" aria-live="polite">${cur ? `<b>${cur}</b> ${cfg.word}` : "아직 안 골랐어요"}</p>
        <button type="button" class="x" data-act="tp-close" data-k="close" aria-label="닫기">×</button></div>
      <div class="tp-sec"><p class="tp-l" id="tp-l1">빠른 선택</p><div class="tp-g tp-quick" role="group" aria-labelledby="tp-l1">${cfg.quick.map((q) => b("tp-quick", q, q, q === cur)).join("")}</div></div>
      <div class="tp-sec"><p class="tp-l" id="tp-l2">시</p><div class="tp-g tp-hours" role="group" aria-labelledby="tp-l2" data-cols="4">${hours.map((h) => b("tp-h", h, `${Number(h)}시`, h === TP.h, ` aria-label="${Number(h)}시"`)).join("")}</div></div>
      <div class="tp-sec"><p class="tp-l" id="tp-l3">분</p><div class="tp-g tp-mins" role="group" aria-labelledby="tp-l3">${mins.map((m) => b("tp-m", m, `${m}분`, TP.h != null && m === (TP.m || "00"))).join("")}</div></div>
      <div class="tp-foot"><button type="button" class="btn sm ghost" data-act="tp-clear" data-k="clear">선택 안 함</button>
        <button type="button" class="btn sm primary" data-act="tp-ok" data-k="ok"${TP.h ? "" : " disabled"}>확인</button></div>`;
    const want = fa ? el.querySelector(`[data-k="${cssEsc(fa)}"]`) : null;
    if (want) want.focus();
    else if (focusFirst) (el.querySelector(".tp-b.on") || el.querySelector(".tp-b")).focus();
  }
  function tpPlace() {
    const el = $("#tp-pop"), back = $("#tp-back");
    if (!el || el.hidden || !TP.trigger) return;
    const sheet = window.innerWidth < 640;
    el.classList.toggle("sheet", sheet);
    back.hidden = !sheet;
    if (sheet) { el.style.left = el.style.top = ""; return; }
    const r = TP.trigger.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    let top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    el.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
    el.style.top = top + "px";
  }
  function tpClose(refocus) {
    const el = $("#tp-pop"), back = $("#tp-back");
    if (el) el.hidden = true;
    if (back) back.hidden = true;
    const t = TP.trigger;
    if (t) { t.setAttribute("aria-expanded", "false"); if (refocus && t.isConnected) t.focus(); }
    TP.trigger = null;
    return t;
  }
  function tpCommit(v) {
    const target = TP.target;
    tpClose(true);
    tpSet(target, v);
  }
  function tpClick(t, act) {
    const v = t.dataset.v;
    if (act === "tp-open") { if (TP.trigger === t) tpClose(true); else tpOpen(t); return; }
    if (act === "tp-close") { tpClose(true); return; }
    if (act === "tp-quick") { TP.h = v.slice(0, 2); TP.m = v.slice(3, 5); }
    else if (act === "tp-h") { TP.h = v; if (!TP.m) TP.m = "00"; }
    else if (act === "tp-m") { TP.m = v; if (!TP.h) TP.h = pad2(TP_CFG[TP.kind].from + 2); }
    else if (act === "tp-clear") { tpCommit(""); return; }
    else if (act === "tp-ok") { if (TP.h) tpCommit(`${TP.h}:${TP.m || "00"}`); return; }
    tpRender(false);
  }
  function tpKeydown(e) {
    if (e.key === "Escape") { e.preventDefault(); tpClose(true); return; }
    const t = e.target;
    const g = t.closest && t.closest(".tp-g");
    if (!g || ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].indexOf(e.key) < 0) return;
    e.preventDefault();
    const items = $$(".tp-b", g), i = items.indexOf(t), cols = Number(g.dataset.cols) || items.length;
    let n = i;
    if (e.key === "ArrowLeft") n = i - 1; else if (e.key === "ArrowRight") n = i + 1;
    else if (e.key === "ArrowUp") n = cols < items.length ? i - cols : i - 1; else if (e.key === "ArrowDown") n = cols < items.length ? i + cols : i + 1;
    else if (e.key === "Home") n = 0; else if (e.key === "End") n = items.length - 1;
    n = Math.max(0, Math.min(items.length - 1, n));
    items[n].focus();
  }

  /* 도착(장소 + 시각) 편집 초안 */
  const ARR = { key: null, label: "", time: "", origLabel: "", origTime: "" };
  function arrInit(c) {
    const p = state.pending[c.key];
    const src = p ? p.parts : null;
    const val = String(c.value || "");
    const origTime = hhmm(val);
    const origLabel = val.replace(/\s*\d{1,2}:\d{2}\s*(까지)?\s*$/, "").replace(/\s*\d{1,2}:\d{2}\s*/, " ").replace(/[\s·,]+$/, "").trim();
    Object.assign(ARR, { key: c.key, origLabel, origTime, label: src ? src.label : origLabel, time: src ? src.time : origTime });
  }
  function arrSubmit(f) {
    const c = currentConds().find((x) => x.key === f.dataset.key);
    if (!c) return;
    const label = (f.querySelector("input[name=place]").value || "").trim();
    const placeChanged = label !== ARR.origLabel || !!condPlace[c.key];
    const timeChanged = ARR.time !== ARR.origTime;
    if (!placeChanged && !timeChanged) { delete state.pending[c.key]; state.openCond = null; renderConditions(); renderActions(); return; }
    let value;
    if (placeChanged) value = ARR.time ? `${label} ${ARR.time}까지` : label;
    else value = ARR.time ? `${ARR.time}까지` : label;
    state.pending[c.key] = { value, place: condPlace[c.key] || null, label: c.label, parts: { label, time: ARR.time } };
    state.openCond = null;
    renderConditions(); renderActions();
    const b = $(`.pill-b[data-key="${cssEsc(c.key)}"]`); if (b) b.focus();
  }

  /* 조건 변경은 바로 보내지 않고 '변경 예정'으로 모았다가 한 번에 보낸다 */
  const condPlace = {}; // 자동완성으로 고른 장소 객체 (key별)
  function sendSet(key, value) {
    const c = currentConds().find((x) => x.key === key);
    const v = String(value);
    // 서버 값과 같으면 '변경 예정'에서 뺀다(같은 형식끼리 비교: 날짜=iso, 시각=HH:MM)
    const same = c && condCurrent(c) === v && !condPlace[key];
    if (same) delete state.pending[key];
    else state.pending[key] = { value: v, place: condPlace[key] || null, label: c ? c.label : key };
    state.openCond = null;
    renderConditions();
    renderActions();
    const b = $(`.pill-b[data-key="${cssEsc(key)}"]`); if (b) b.focus();
  }
  const pendingCount = () => Object.keys(state.pending).length;
  const pendingShow = (key, v) => { if (v === "") return "선택 안 함"; const c = currentConds().find((x) => x.key === key); return c && c.input === "date" ? fmtDate(v) : v; };
  function applyPending() {
    const keys = Object.keys(state.pending);
    if (!keys.length) return;
    const changes = keys.map((k) => { const p = state.pending[k]; const o = { key: k, value: p.value }; if (p.place) o.place = p.place; return o; });
    const q = keys.map((k) => `${state.pending[k].label} → ${pendingShow(k, state.pending[k].value)}`).join(", ");
    send({ question: `[조건 변경] ${q}`, action: "set", set: { changes } }, "set");
  }
  function pendingBar() {
    const n = pendingCount();
    if (!n || READ_ONLY) return "";
    return `<div class="pend-bar"><p><b>변경 예정 ${n}건</b><span>아직 계획에 반영되지 않았어요</span></p>
      <button type="button" class="btn sm ghost" data-act="cond-clear">모두 취소</button>
      <button type="button" class="btn sm primary" data-act="cond-apply"${state.busy ? " disabled" : ""}>변경 ${n}건 · 계획 다시 세우기</button></div>`;
  }

  /* ───────── 브로셔 ───────── */
  function placeIndex(plan) {
    const m = new Map();
    (plan.places || []).forEach((p) => m.set(String(p.id), p));
    return m;
  }
  const dayColor = (day) => DAY_COLORS[(Number(day) - 1 + DAY_COLORS.length) % DAY_COLORS.length];

  function findLeg(day, stop) {
    return (day.legs || []).find((l) => (stop.place_id != null && String(l.to) === String(stop.place_id)) || (l.to_name && l.to_name === stop.name));
  }

  function renderBrochure(opt) {
    const el = $("#brochure");
    const plan = state.plan;
    if (!el || !plan) return;
    if (opt && opt.moveAfter && opt.moveAfter.parentNode) opt.moveAfter.after(el);
    stashPanels(); // 모바일에서 브로셔 안으로 옮겨 둔 지도·경비·버튼 패널을 다시 그리기 전에 꺼내 둔다
    const openIds = new Set($$(".stop.open", el).map((s) => s.id));
    const P = placeIndex(plan);
    const trip = plan.trip || {};
    const days = plan.itinerary || [];
    const ld = (plan.totals && plan.totals.long_distance) || {};
    const lastDay = days.length ? days[days.length - 1].day : null;

    const html = [];
    html.push(tripHeader(plan));
    html.push(`<div id="m-map-slot" class="m-map"><button type="button" class="m-toggle" data-act="m-map" aria-expanded="${state.mobileMapOpen}" aria-controls="m-map-body">
      ${ico("ico-pin")}<span>지도 보기</span><span class="m-sub">날짜별 동선과 순서</span>${ico("ico-chev", "ico chev")}</button><div id="m-map-body" class="m-body"${state.mobileMapOpen ? "" : " hidden"}></div></div>`);
    if (ld.outbound) html.push(ticket(ld.outbound, "가는 길", trip.start_date));
    html.push(transportGuide(plan.transport_guide));
    days.forEach((d) => {
      html.push(daySection(d, P, plan));
      if (d.day === lastDay && ld.inbound) html.push(ticket(ld.inbound, "오는 길", d.date));
    });
    if (canEdit() && days.length) html.push(addByName(days));
    if ((plan.warnings || []).length) {
      html.push(`<section class="warn-box" aria-labelledby="w-h"><h3 id="w-h">모모가 미리 챙긴 주의할 점</h3><ul>${plan.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></section>`);
    }
    if (plan.costs) html.push(costTable(plan.costs));
    html.push(packingSection(plan));
    const cands = (plan.places || []).filter((p) => !p.in_itinerary).sort((a, b) => (a.rank || 99) - (b.rank || 99));
    if (cands.length) html.push(candidates(cands, days));
    html.push(`<div id="m-act-slot"></div>`);
    html.push(`<footer class="sources"><p>장소·운영시간·사진: 한국관광공사 TourAPI(공공누리). 이동시간은 직선거리 기반 추정이에요. 지도: © OpenStreetMap contributors.</p></footer>`);
    el.innerHTML = html.join("");
    el.hidden = false;
    openIds.forEach((id) => { const s = document.getElementById(id); if (s) setOpen(s, true); });
    if (PRINT) $$(".stop", el).forEach((s) => setOpen(s, true));
    placePanels();
    renderMap();
    observeStops();
    if (READ_ONLY) renderExpenses();
  }

  function tripHeader(plan) {
    const t = plan.trip || {};
    const days = plan.itinerary || [];
    const endIso = days.length ? days[days.length - 1].date : null;
    const comp = t.companions || {};
    const tr = t.transport || {};
    const season = seasonOf(t.start_date);
    const local = (plan.totals && plan.totals.local) || {};
    const nStops = days.reduce((a, d) => a + (d.stops || []).filter((s) => s.type === "place").length, 0);
    const statusTxt = state.planStatus === "확정" ? "확정된 여행 계획서" : "여행 계획서 초안 · 확정 전";
    const fv = !READ_ONLY && fullViewUrl();
    const facts = [
      ["출발", t.origin ? `${t.origin} → ${(t.regions || []).join("·") || ""}` : "현지 도착 기준"],
      ["강도", t.intensity],
      ["동반", [comp.type, comp.count ? comp.count + "명" : "", comp.note].filter(Boolean).join(" · ")],
      ["이동", [tr.long_distance, tr.local].filter(Boolean).join(" + ")],
    ].filter((f) => f[1]);
    const d0 = t.start_date ? new Date(t.start_date + "T00:00:00") : null;
    const pm = d0 && !isNaN(d0) ? `${d0.getFullYear()}.${String(d0.getMonth() + 1).padStart(2, "0")}.${String(d0.getDate()).padStart(2, "0")}` : "";
    return `<header class="trip" aria-labelledby="trip-h">
      <div class="airmail" aria-hidden="true"></div>
      <div class="trip-in">
        <div class="stamp" aria-hidden="true"><div class="stamp-in">${bird()}<span>${SEASON_LABEL[season]} 편</span></div></div>
        ${pm ? `<svg class="postmark" viewBox="0 0 120 70" aria-hidden="true"><circle cx="35" cy="35" r="28" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="35" cy="35" r="22" fill="none" stroke="currentColor" stroke-width=".8"/>
          <text x="35" y="33" text-anchor="middle" font-size="8.5" fill="currentColor" font-family="inherit">${esc(pm)}</text><text x="35" y="44" text-anchor="middle" font-size="6.5" fill="currentColor">MOMO POST</text>
          <path d="M68 22 q8 -5 16 0 t16 0 t16 0 M68 35 q8 -5 16 0 t16 0 t16 0 M68 48 q8 -5 16 0 t16 0 t16 0" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>` : ""}
        <p class="eyebrow">${esc(statusTxt)}</p>
        <h2 id="trip-h" class="display">${esc(t.title || "여행 계획서")}</h2>
        <p class="trip-dates">${fmtDate(t.start_date, true)}${endIso && endIso !== t.start_date ? ` – ${fmtDate(endIso)}` : ""}</p>
        <dl class="facts">${facts.map((f) => `<div><dt>${f[0]}</dt><dd>${esc(f[1])}</dd></div>`).join("")}</dl>
        <p class="trip-stats"><span><b>${nStops}</b>곳 방문</span>${local.distance_km != null ? `<span>현지 이동 <b>${esc(km(local.distance_km))}</b></span><span>약 <b>${fmtMin(local.travel_min)}</b> (추정)</span>` : ""}</p>
        ${weatherStrip(plan)}
        ${fv ? `<a class="btn sm ghost fv-btn noprint" href="${esc(fv)}" target="_blank" rel="noopener">${ico("ico-pop")}전체 화면으로 보기</a>` : ""}
        ${t.route && t.route.note ? `<p class="route-note"><span class="badge route">동선</span>${esc(t.route.note)}</p>` : ""}
        ${(t.assumptions || []).length ? `<div class="assume"><span class="assume-t">모모가 가정한 것</span><ul>${t.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>` : ""}
      </div></header>`;
  }

  function ticket(leg, kind, dateIso) {
    const [from, to] = String(leg.label || "").split(/\s*→\s*/);
    return `<section class="ticket" aria-label="${esc(kind)} ${esc(leg.label || "")}">
      <div class="tk-main">
        <p class="tk-kind">${esc(kind)} <span>${kind === "가는 길" ? "OUTBOUND" : "INBOUND"}</span></p>
        <div class="tk-route">
          <div><span class="tk-time">${esc(leg.depart || "")}</span><span class="tk-city">${esc(from || "")}</span></div>
          <div class="tk-line" aria-hidden="true"><span></span></div>
          <div class="r"><span class="tk-time">${esc(leg.arrive || "")}</span><span class="tk-city">${esc(to || "")}</span></div>
        </div>
      </div>
      <div class="tk-stub">
        <span class="tk-mode">${esc(leg.mode || "")}</span>
        <span class="tk-dur">약 ${fmtMin(leg.duration_min)}${leg.estimated !== false ? "<small>(추정)</small>" : ""}</span>
        ${dateIso ? `<span class="tk-date">${fmtDate(dateIso)}</span>` : ""}
      </div></section>`;
  }

  function daySection(d, P, plan) {
    const color = dayColor(d.day);
    const stops = d.stops || [];
    const items = stops.map((s, i) => {
      const leg = findLeg(d, s);
      const legHtml = leg ? `<li class="leg" aria-label="이동 ${esc(km(leg.distance_km))}, 약 ${fmtMin(leg.duration_min)}">
          <span class="leg-in">${i === 0 && leg.from_name ? `<span class="leg-from">${esc(leg.from_name)}에서</span>` : ""}▶ ${esc(km(leg.distance_km))} · 약 ${fmtMin(leg.duration_min)} · ${esc(leg.mode || "")}${leg.estimated !== false ? " (추정)" : ""}</span></li>` : "";
      return legHtml + stopCard(s, i, stops.length, d, P, plan);
    }).join("");
    const nPlaces = stops.filter((s) => s.type === "place").length;
    const route = (plan.trip && plan.trip.route) || {};
    const mineOrder = (route.user_ordered_days || []).map(Number).indexOf(Number(d.day)) >= 0;
    const rBadge = mineOrder ? `<span class="badge user" title="직접 조정한 순서를 가장 먼저 지켜요">내가 정한 순서</span>`
      : route.optimized ? `<span class="badge route" title="이동이 짧아지도록 방문 순서를 정리했어요">동선 최적화</span>` : "";
    return `<section class="day" id="day-${esc(d.day)}" data-day="${esc(d.day)}" style="--day:${color}" aria-labelledby="day-h-${esc(d.day)}">
      <header class="day-h">
        <span class="day-no"><b>${esc(d.day)}</b>일차</span>
        <div><h3 id="day-h-${esc(d.day)}">${fmtDate(d.date)}${d.theme ? ` <span class="theme">${esc(d.theme)}</span>` : ""}</h3>
        <p>이동 약 ${fmtMin(d.day_travel_min)} · ${esc(km(d.day_distance_km || 0))} · 관광 ${nPlaces}곳 ${rBadge}</p></div>
        ${wxChip(weatherOf(d.date))}
      </header>
      <ol class="stops">${items || `<li class="empty">이 날은 아직 비어 있어요.</li>`}</ol></section>`;
  }

  function licenseOf(img) { return LICENSE[img.license] || img.license || "공공누리"; }
  function mediaHtml(place, alt) {
    const imgs = (place && place.images || []).filter((i) => i && (i.url || i.thumb)).slice(0, 4);
    if (!imgs.length) return `<figure class="media none">${MOMO_ILLUST}<figcaption>사진 준비 중 · 모모 그림</figcaption></figure>`;
    const first = imgs[0];
    const cap = (im) => `사진: ${esc(im.credit || "한국관광공사")} · ${esc(licenseOf(im))}`;
    const thumbs = imgs.length > 1 ? `<div class="thumbs">${imgs.map((im, k) => `<button type="button" class="thumb${k === 0 ? " on" : ""}${im.license === "Type3" ? " contain" : ""}" data-act="thumb" data-src="${esc(im.url || im.thumb)}" data-cap="${cap(im)}" data-l3="${im.license === "Type3" ? 1 : 0}" aria-label="사진 ${k + 1} 크게 보기 (${cap(im)})"><img src="${esc(im.thumb || im.url)}" alt="" loading="lazy" decoding="async"></button>`).join("")}</div>` : "";
    return `<figure class="media${first.license === "Type3" ? " contain" : ""}">
      <div class="main-img"><img src="${esc(first.url || first.thumb)}" alt="${esc(alt)} 사진" loading="${PRINT ? "eager" : "lazy"}" decoding="async"></div>
      <figcaption>${cap(first)}</figcaption>${thumbs}</figure>`;
  }

  function stopCard(s, i, n, d, P, plan) {
    const p = s.place_id != null ? P.get(String(s.place_id)) : null;
    const id = `stop-${d.day}-${s.order != null ? s.order : i + 1}`;
    const time = s.arrive === s.depart || !s.depart ? `${esc(s.arrive || "")}${s.type === "stay" ? " 체크인" : ""}` : `${esc(s.arrive || "")}–${esc(s.depart || "")}`;
    const num = `<span class="num" aria-hidden="true">${s.type === "meal" ? ico("ico-fork") : s.type === "stay" ? "숙" : esc(s.order != null ? s.order : i + 1)}</span>`;
    if (!p && s.place_id == null) {
      return `<li class="stop slim t-${esc(s.type)}" id="${id}" data-day="${esc(d.day)}" data-pid="">${num}
        <div class="card slim-card"><span class="time">${time}</span><span class="tag">${esc(TYPE_LABEL[s.type] || "")}</span><b>${esc(s.name)}</b>${s.reason ? `<span class="muted">${esc(s.reason)}</span>` : ""}</div></li>`;
    }
    const place = p || {};
    const name = place.name || s.name;
    const cat = place.category || TYPE_LABEL[s.type] || "";
    const hoursBadge = s.hours_status === "확인 필요"
      ? `<span class="badge warn">운영시간 확인 필요</span>`
      : place.hours ? `<span class="badge ok">운영 ${esc(place.hours)}</span>` : "";
    const rank = place.rank ? `<span class="badge rank">추천 ${esc(place.rank)}위</span>` : "";
    const mine = place.added_by_user ? `<span class="badge user">직접 추가</span>` : "";
    const reason = s.reason || place.reason;
    const comp = (plan.trip && plan.trip.companions) || {};
    const compLabel = comp.type === "노약자" ? "노약자 동반 참고" : /어린이|유아/.test(comp.type || "") ? "아이 동반 참고" : "동반자 참고";
    const info = [["휴무", place.closed], ["요금", place.fee], ["주차", place.parking], ["문의", place.contact]].filter((x) => x[1]);
    const chips = (arr, food) => (arr || []).map((x) => `<li class="chip-s" title="${esc(x.note || "")}">${esc(x.name)}${x.distance_km != null ? ` · ${esc(x.distance_km)}km` : ""}${food && x.note ? ` <span class="muted">· ${esc(x.note)}</span>` : ""}</li>`).join("");
    const details = [
      (place.highlights || []).length ? `<div class="d-block"><h5>이곳만의 볼거리</h5><ul class="hl">${place.highlights.map((h) => `<li>${esc(h)}</li>`).join("")}</ul></div>` : "",
      place.guide_tips ? `<div class="d-block"><h5>관람 팁</h5><p>${esc(place.guide_tips)}</p></div>` : "",
      place.companion_notes ? `<div class="d-block comp"><h5>${esc(compLabel)}</h5><p>${esc(place.companion_notes)}</p></div>` : "",
      (place.nearby_sights || []).length ? `<div class="d-block"><h5>근처 볼거리</h5><ul class="chips-s">${chips(place.nearby_sights)}</ul></div>` : "",
      (place.foods || []).length ? `<div class="d-block"><h5>근처 먹거리</h5><ul class="chips-s">${chips(place.foods, true)}</ul></div>` : "",
      info.length ? `<dl class="info">${info.map((x) => `<div><dt>${x[0]}</dt><dd>${esc(x[1])}</dd></div>`).join("")}</dl>` : "",
      place.address ? `<p class="addr">${esc(place.address)}</p>` : "",
    ].join("");
    const hasDetails = !!details.trim();
    const edit = canEdit() && s.place_id != null ? `<div class="edit" role="group" aria-label="${esc(name)} 편집">
        <button type="button" class="btn xs ghost" data-act="up" data-pid="${esc(s.place_id)}" data-day="${esc(d.day)}" ${i === 0 ? "disabled" : ""} aria-label="${esc(name)} 앞으로">▲</button>
        <button type="button" class="btn xs ghost" data-act="down" data-pid="${esc(s.place_id)}" data-day="${esc(d.day)}" ${i === n - 1 ? "disabled" : ""} aria-label="${esc(name)} 뒤로">▼</button>
        ${(plan.itinerary || []).length > 1 ? `<span class="move-wrap"><label class="sr" for="mv-${id}">옮길 날</label><select id="mv-${id}" class="sel xs">${(plan.itinerary || []).filter((x) => x.day !== d.day).map((x) => `<option value="${esc(x.day)}">${esc(x.day)}일차로</option>`).join("")}</select><button type="button" class="btn xs ghost" data-act="move" data-pid="${esc(s.place_id)}">다른 날로</button></span>` : ""}
        <button type="button" class="btn xs danger" data-act="remove" data-pid="${esc(s.place_id)}" data-day="${esc(d.day)}" data-name="${esc(name)}">빼기</button></div>` : "";
    return `<li class="stop t-${esc(s.type)}" id="${id}" data-day="${esc(d.day)}" data-pid="${esc(s.place_id)}">${num}
      <article class="card" aria-labelledby="${id}-h">
        ${mediaHtml(p, name)}
        <div class="body">
          <p class="meta"><span class="time">${time}</span><span class="tag">${esc(cat)}</span>${rank}${mine}${hoursBadge}</p>
          <h4 id="${id}-h">${esc(name)}</h4>
          ${reason ? `<p class="reason">${esc(reason)}</p>` : ""}
          ${place.summary ? `<p class="summary">${esc(place.summary)}</p>` : ""}
          ${hasDetails ? `<div class="details" id="${id}-d">${details}</div>` : ""}
          <div class="card-foot">
            ${hasDetails ? `<button type="button" class="more" data-act="more" aria-expanded="false" aria-controls="${id}-d">자세히${ico("ico-chev", "ico chev")}</button>` : ""}
            <span class="src">출처: ${esc(place.source || s.source || "한국관광공사 TourAPI")}</span>
          </div>
          ${edit}
        </div>
      </article></li>`;
  }

  function setOpen(stopEl, on) {
    stopEl.classList.toggle("open", on);
    const b = $(".more", stopEl);
    if (b) { b.setAttribute("aria-expanded", String(on)); b.firstChild.textContent = on ? "접기" : "자세히"; }
  }

  function daySelect(days, id, withAuto) {
    return `<select class="sel xs" id="${id}">${withAuto ? `<option value="">알맞은 날에</option>` : ""}${days.map((x) => `<option value="${esc(x.day)}">${esc(x.day)}일차</option>`).join("")}</select>`;
  }

  /* 장소 찾기 — 정확한 이름을 몰라도 검색해서 넣기 */
  function addByName(days) {
    return `<section class="add-name" aria-labelledby="an-h">
      <h3 id="an-h">가고 싶은 곳이 더 있나요?</h3>
      <p class="muted">이름 일부만 적어도 찾아 드려요. 가까운 순으로 보여 드릴게요.</p>
      <form class="row" data-form="place-search" role="search">
        <label class="sr" for="an-in">장소 찾기</label>
        <input id="an-in" name="q" type="search" data-psearch="1" value="${esc(state.search.q)}" placeholder="예) 사성, 낙안, 꼬막" maxlength="40" autocomplete="off" aria-controls="ps-results">
        <button class="btn sm ghost">찾기</button></form>
      <div id="ps-results" class="ps-results" aria-live="polite">${searchResultsHtml(days)}</div>
      <div id="ps-manual">${manualHtml(days)}</div></section>`;
  }
  /* 검색에 없을 때: 이름 + (주소 자동완성 또는 지도에서 찍기)로 직접 추가 */
  function manualHtml(days) {
    const Mn = state.manual;
    days = days || (state.plan && state.plan.itinerary) || [];
    const open = Mn.open || (state.search.manual_add && !Mn.closed);
    const coord = Mn.lat != null ? `${Number(Mn.lat).toFixed(5)}, ${Number(Mn.lng).toFixed(5)}${Mn.src === "map" ? " (지도에서 찍음)" : ""}` : "위치를 아직 정하지 않았어요";
    return `<div class="ps-man">
      <button type="button" class="linkish" data-act="man-toggle" aria-expanded="${!!open}" aria-controls="man-form">찾는 곳이 없나요? 직접 추가</button>
      ${open ? `<form id="man-form" class="man-form" data-form="manual" aria-label="장소 직접 추가">
        <label class="st-in"><span>장소 이름</span><input type="text" name="name" data-man="name" value="${esc(Mn.name)}" maxlength="40" required autocomplete="off" placeholder="예) 1001카페"></label>
        <label class="st-in"><span>주소 <small class="muted">선택 · 고르면 위치가 정해져요</small></span><input type="text" name="address" data-man="address" value="${esc(Mn.address)}" maxlength="60" autocomplete="off" placeholder="도로명이나 동 이름"${geoAttrs("manual")}></label>
        <div class="man-pick"><button type="button" class="btn sm ghost${Mn.picking ? " on" : ""}" data-act="man-pick" aria-pressed="${!!Mn.picking}">${ico("ico-pin")}${Mn.picking ? "지도를 눌러 주세요…" : "지도에서 위치 찍기"}</button>
          <span class="man-coord${Mn.lat != null ? " set" : ""}">${esc(coord)}</span></div>
        <div class="man-go"><label class="sr" for="man-day">넣을 날</label>${daySelect(days, "man-day", true)}<button class="btn sm primary">추가</button></div>
        <p class="small muted">사용자 지정 장소로 넣어요. 운영시간·휴무는 확인이 필요해요.</p></form>` : ""}</div>`;
  }
  function paintManual() { const b = $("#ps-manual"); if (b) b.innerHTML = manualHtml(); showManualPin(); }
  function showManualPin() {
    if (!M.map || !window.L) return;
    if (M.manualPin) { M.map.removeLayer(M.manualPin); M.manualPin = null; }
    const Mn = state.manual;
    if (Mn.lat == null) return;
    M.manualPin = L.marker([Number(Mn.lat), Number(Mn.lng)], { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk search">+</span>`, iconSize: [24, 24], iconAnchor: [12, 12] }), title: `직접 추가: ${Mn.name || "새 장소"}` }).addTo(M.map);
  }
  function startMapPick() {
    const Mn = state.manual;
    if (!window.L) { toast("지도를 불러오지 못해 위치를 찍을 수 없어요. 주소로 골라 주세요."); return; }
    if (!MQ_PC.matches && !state.mobileMapOpen) { const mb = $('[data-act="m-map"]'); if (mb) mb.click(); }
    if (!M.map) return;
    Mn.picking = true; paintManual();
    M.map.getContainer().classList.add("picking");
    toast("지도에서 넣을 위치를 눌러 주세요");
    { const mp = $("#map"); if (mp && !MQ_PC.matches) mp.scrollIntoView({ behavior: scrollBehavior(), block: "center" }); }
    M.map.once("click", (e) => {
      Object.assign(Mn, { lat: e.latlng.lat, lng: e.latlng.lng, src: "map", picking: false });
      M.map.getContainer().classList.remove("picking");
      paintManual();
    });
  }
  function searchResultsHtml(days) {
    const R = state.search;
    days = days || (state.plan && state.plan.itinerary) || [];
    if (R.loading) return `<p class="muted small">찾는 중이에요…</p>`;
    if (!R.q || R.q.length < 2) return "";
    if (R.error) return `<p class="muted small">${esc(R.error)}</p>`;
    const list = R.results || [];
    const srcLine = (R.sources || []).length ? `<p class="ps-var">찾아본 곳: ${R.sources.map(esc).join(" · ")}</p>` : "";
    const fallback = `<p class="ps-none">원하는 곳이 없나요? <button type="button" class="linkish" data-act="ps-name" data-q="${esc(R.q)}">'${esc(R.q)}' 이름 그대로 모모에게 찾아 달라고 하기</button></p>`;
    const vars = (R.variants || []).filter((v) => v && v !== R.q);
    const varLine = vars.length ? `<p class="ps-var">찾은 이름 변형: ${vars.map(esc).join(" · ")}</p>` : "";
    // 여행 지역에서 먼 곳만 찾았을 때: 모모가 포함할지 묻는 확인 카드
    const far = R.farDismissed ? [] : (R.far || []);
    const farCard = (R.far_message && !R.farDismissed) || (!list.length && far.length) ? `<div class="ps-far" role="group" aria-label="먼 곳 확인">
        <div class="avatar">${bird()}</div>
        <div class="ps-far-b"><p class="ps-far-msg">${esc(R.far_message || "여행 지역에서 먼 곳만 찾았어요. 일정에 포함할까요?")}</p>
        ${far.length ? `<ul class="ps-list">${far.map((r, i) => `<li class="ps-item far">
          <div class="ps-img">${r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy">` : bird("bird")}</div>
          <div class="ps-body"><p class="ps-name"><b>${esc(r.name)}</b><span class="badge warn">약 ${esc(Number(r.distance_km || 0).toFixed(0))}km · 직선</span>${r.source ? `<span class="tag">${esc(r.source)}</span>` : ""}</p>
            ${r.address ? `<p class="ps-meta">${esc(r.address)}</p>` : ""}</div>
          <div class="ps-add"><label class="sr" for="pf-d-${i}">넣을 날</label>${daySelect(days, "pf-d-" + i, true).replace('class="sel xs"', 'class="sel xs ps-far-day"')}
            <button type="button" class="btn xs primary" data-act="ps-add-far" data-i="${i}">포함할게요</button>
            <button type="button" class="btn xs ghost" data-act="ps-far-no">아니요</button></div></li>`).join("")}</ul>` : ""}
        </div></div>` : "";
    if (!list.length) return farCard ? farCard + varLine + srcLine : `<p class="muted small">'${esc(R.q)}'와 비슷한 곳을 찾지 못했어요.</p>${varLine}${srcLine}${fallback}`;
    return `${farCard}${R.note ? `<p class="muted small">${esc(R.note)}</p>` : ""}<ul class="ps-list">${list.map((r, i) => {
      const src = String(r.source || "");
      const osm = /openstreetmap|osm/i.test(src) || String(r.key || "").indexOf("osm:") === 0;
      const kakao = /카카오|kakao/i.test(src), naver = /네이버|naver/i.test(src);
      const badge = kakao ? ["kakao", "카카오맵"] : naver ? ["naver", "네이버"] : osm ? ["draft", "지도"] : ["route", "관광공사"];
      const ext = /^https:\/\//i.test(r.place_url || "") ? `<a class="ps-ext" href="${esc(r.place_url)}" target="_blank" rel="noopener">${kakao ? "카카오맵에서 보기" : naver ? "네이버에서 보기" : "자세히 보기"} ${ico("ico-pop")}</a>` : "";
      return `<li class="ps-item">
        <div class="ps-img">${r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy" title="${esc(r.image_source ? "사진: " + r.image_source : "")}">` : bird("bird")}</div>
        <div class="ps-body"><p class="ps-name"><b>${esc(r.name)}</b><span class="badge ${badge[0]}">${badge[1]}</span>${r.category ? `<span class="tag">${esc(r.category)}</span>` : ""}</p>
          <p class="ps-meta">${r.distance_km != null ? `약 ${esc(Number(r.distance_km).toFixed(1))}km` : ""}${r.address ? ` · ${esc(r.address)}` : ""}</p>
          ${r.phone || ext || (r.image && r.image_source) ? `<p class="ps-meta2">${r.phone ? `<span>${esc(r.phone)}</span>` : ""}${ext}${r.image && r.image_source ? `<span class="muted">사진: ${esc(r.image_source)}</span>` : ""}</p>` : ""}
          ${osm || kakao || naver ? `<p class="ps-warn">${osm ? "지도 정보라" : "외부 지도 정보라"} 운영시간 확인이 필요해요</p>` : ""}</div>
        <div class="ps-add"><label class="sr" for="ps-d-${i}">넣을 날</label>${daySelect(days, "ps-d-" + i, true)}
          <button type="button" class="btn xs primary" data-act="ps-add" data-i="${i}">넣기</button></div></li>`;
    }).join("")}</ul>${varLine}${srcLine}${fallback}`;
  }
  let psTimer = 0, psSeq = 0;
  function placeSearch(q, immediate) {
    q = String(q || "").trim();
    state.search.q = q;
    clearTimeout(psTimer);
    if (q.length < 2) { state.search.results = []; state.search.loading = false; paintSearch(); return; }
    psTimer = setTimeout(async () => {
      const seq = ++psSeq;
      state.search.loading = true; paintSearch();
      let j = null;
      try {
        if (DEMO) {
          const r = await fetch("/static/sample_search.json").catch(() => null);
          j = r && r.ok ? await r.json() : null;
          if (!j) { // 견본 검색 파일이 없으면 견본 계획서의 장소로 흉내
            const pl = (state.plan && state.plan.places) || [];
            j = { query: q, results: pl.filter((p) => p.name.indexOf(q.slice(0, 2)) >= 0).map((p, k) => ({ key: "tour:" + p.id, source: "관광공사(수집)", name: p.name, address: p.address, lat: p.lat, lng: p.lng, distance_km: 3 + k * 4.2, image: (p.images[0] || {}).thumb || null, category: p.category, place_id: p.id })) };
          }
        } else {
          const region = (state.plan && state.plan.trip && state.plan.trip.region_id) || "";
          const r = await fetch(`/places/search?q=${encodeURIComponent(q)}&region=${encodeURIComponent(region)}&session_id=${encodeURIComponent(state.sessionId || "")}`);
          j = await r.json().catch(() => null);
          if (!r.ok) throw new Error((j && j.answer) || "검색하지 못했어요");
        }
        if (seq !== psSeq) return;
        state.search = { q, results: ((j && j.results) || []).slice().sort((a, b) => (a.distance_km == null ? 1e9 : a.distance_km) - (b.distance_km == null ? 1e9 : b.distance_km)), note: j && j.note, loading: false,
          far: (j && j.far) || [], far_message: (j && j.far_message) || "", variants: (j && j.variants) || [], radius_km: j && j.radius_km,
          sources: (j && j.sources) || [], manual_add: !!(j && j.manual_add) };
      } catch (e) {
        if (seq !== psSeq) return;
        state.search = { q, results: [], loading: false, error: e.message || "검색하지 못했어요. 잠시 후 다시 해 주세요." };
      }
      paintSearch();
    }, immediate ? 0 : 400);
  }
  function paintSearch() {
    const box = $("#ps-results");
    if (box) box.innerHTML = searchResultsHtml();
    const mb = $("#ps-manual");
    if (mb && !(mb.contains(document.activeElement))) mb.innerHTML = manualHtml();
    showSearchMarkers();
  }

  function candidates(cands, days) {
    const edit = canEdit() && days.length;
    return `<section class="cands" aria-labelledby="cand-h"><h3 id="cand-h">후보 장소 <span class="muted">일정에 넣지 않은 추천</span></h3>
      <ul class="cand-grid">${cands.map((p) => {
        const im = (p.images || [])[0];
        const fig = im ? `<figure class="c-img${im.license === "Type3" ? " contain" : ""}"><img src="${esc(im.thumb || im.url)}" alt="${esc(p.name)} 사진" loading="lazy" decoding="async"><figcaption>사진: ${esc(im.credit || "한국관광공사")} · ${esc(licenseOf(im))}</figcaption></figure>` : `<figure class="c-img none">${MOMO_ILLUST}</figure>`;
        return `<li class="cand">${fig}<div class="c-body">
          <p class="meta">${p.rank ? `<span class="badge rank">추천 ${esc(p.rank)}위</span>` : ""}<span class="tag">${esc(p.category || "")}</span>${p.hours ? `<span class="badge ok">운영 ${esc(p.hours)}</span>` : ""}</p>
          <h4>${esc(p.name)}</h4>${p.reason ? `<p class="reason">${esc(p.reason)}</p>` : ""}
          ${edit ? `<div class="c-add"><label class="sr" for="cd-${esc(p.id)}">넣을 날</label>${daySelect(days, "cd-" + esc(p.id), true)}<button type="button" class="btn xs primary" data-act="add" data-pid="${esc(p.id)}" data-name="${esc(p.name)}">일정에 넣기</button></div>` : ""}
        </div></li>`;
      }).join("")}</ul></section>`;
  }

  function sendEdit(edit, desc) {
    send({ question: `[화면 편집] ${desc}`, action: "edit", edit }, "edit");
  }

  /* ───────── 경비 ───────── */
  const ADD_COST_CATS = ["식비", "카페·디저트", "간식·음료", "기념품", "체험", "주차비", "기타"];
  const canCost = () => !READ_ONLY && !!state.plan;
  const costDel = (it) => (it.custom && it.id != null && canCost())
    ? `<button type="button" class="del" data-act="cost-del" data-id="${esc(it.id)}" data-name="${esc(it.label)}" aria-label="${esc(it.label)} 비용 빼기">×</button>` : "";
  function costRows(costs) {
    return (costs.items || []).map((it) => `<tr${it.custom ? ' class="custom"' : ""}><th scope="row"><span class="cat">${esc(it.category)}</span>${esc(it.label)}${it.custom ? ` <span class="badge user">직접 추가</span>` : ""}${it.basis ? `<small>${esc(it.basis)}</small>` : ""}</th>
      <td class="num-c">${it.unknown ? `<span class="badge warn">확인 필요</span>` : won(it.amount) + (it.estimated && !it.custom ? `<small>추정</small>` : "")}${costDel(it)}</td></tr>`).join("");
  }
  function costAddForm(id) {
    return `<form class="cost-add noprint" data-form="cost-add" aria-label="비용 추가">
      <label class="sr" for="${id}-c">분류</label><select id="${id}-c" name="category" class="sel">${ADD_COST_CATS.map((c) => `<option>${esc(c)}</option>`).join("")}</select>
      <label class="sr" for="${id}-l">내용</label><input id="${id}-l" name="label" type="text" maxlength="40" placeholder="내용 (예: 카페 음료 4잔)" required autocomplete="off">
      <label class="sr" for="${id}-a">금액(원)</label><input id="${id}-a" name="amount" type="number" inputmode="numeric" min="0" step="100" placeholder="금액(원)" required>
      <button class="btn sm ghost">비용 추가</button></form>`;
  }
  function budgetBar(c) {
    if (!c.budget) return "";
    const pct = Math.round((c.total / c.budget) * 100);
    const over = c.over_budget || c.total > c.budget;
    return `<div class="budget${over ? " over" : ""}"><div class="b-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.min(pct, 100)}" aria-label="예산 대비 ${pct}%"><span style="width:${Math.min(pct, 100)}%"></span></div>
      <p>${over ? `예산 ${won(c.budget)}을 <b>${won(c.total - c.budget)}</b> 넘어요` : `예산 ${won(c.budget)}의 <b>${pct}%</b>`}</p></div>`;
  }
  function costTable(costs) {
    return `<section class="costs-b" aria-labelledby="cost-h"><h3 id="cost-h">예상 경비 <span class="muted">${esc(costs.people || "")}${costs.people ? "명 기준 · 모두 추정" : ""}</span></h3>
      <table class="tbl"><tbody>${costRows(costs)}</tbody>
      <tfoot><tr><th scope="row">합계</th><td class="num-c">${won(costs.total)}</td></tr>
      ${costs.per_person != null ? `<tr><th scope="row">1인당</th><td class="num-c">${won(costs.per_person)}</td></tr>` : ""}
      ${costs.budget ? `<tr class="${costs.over_budget ? "over" : ""}"><th scope="row">최대 예산</th><td class="num-c">${won(costs.budget)}${costs.over_budget ? ` <small>예산 초과</small>` : ""}</td></tr>` : ""}</tfoot></table>
      ${costs.note ? `<p class="muted small">${esc(costs.note)}</p>` : ""}
      ${canCost() ? `<div class="cost-add-w"><p class="small muted">카페·간식·기념품처럼 따로 쓸 돈도 넣어 두면 합계에 반영돼요.</p>${costAddForm("cb")}</div>` : ""}</section>`;
  }
  function renderCosts() {
    const el = $("#cost-panel");
    if (!el) return;
    const c = state.plan && state.plan.costs;
    if (!c) { el.hidden = true; el.innerHTML = ""; return; }
    el.hidden = false;
    // 분류별 합계(직접 추가 항목은 따로 보여 빼기 가능)
    const by = [];
    (c.items || []).filter((it) => !it.custom).forEach((it) => {
      let g = by.find((x) => x.cat === it.category);
      if (!g) { g = { cat: it.category, sum: 0, unknown: 0, n: 0 }; by.push(g); }
      g.n++; if (it.unknown) g.unknown++; else g.sum += Number(it.amount) || 0;
    });
    const customs = (c.items || []).filter((it) => it.custom);
    const wasOpen = el.querySelector("details[open]") != null;
    el.innerHTML = `<div class="cost-line"><h2 class="ph-title">예상 경비</h2><b class="cost-sum">${won(c.total)}</b><span class="muted">1인 ${won(c.per_person)}${c.people ? ` · ${esc(c.people)}명` : ""}</span></div>
      ${budgetBar(c)}
      <details${wasOpen ? " open" : ""}><summary>분류별 금액${canCost() ? " · 비용 추가" : ""}</summary>
        <ul class="cost-list">${by.map((g) => `<li><span>${esc(g.cat)}${g.n > 1 ? `<small>${g.n}건${g.unknown ? ` · 확인 필요 ${g.unknown}` : ""}</small>` : ""}</span><b>${g.sum ? won(g.sum) : g.unknown ? "확인 필요" : won(0)}</b></li>`).join("")}
        ${customs.map((it) => `<li class="custom"><span>${esc(it.category)}<small>${esc(it.label)} · 직접 추가</small></span><b>${won(it.amount)}${costDel(it)}</b></li>`).join("")}</ul>
        ${canCost() ? costAddForm("cp") : ""}</details>`;
  }
  /* ───────── 날씨 ───────── */
  function weatherOf(date) {
    const W = (state.plan && state.plan.weather) || [];
    return W.find((w) => w.date === date) || null;
  }
  function wxChip(w, withDate) {
    if (!w) return "";
    const t = [w.tmin != null ? `${w.tmin}°` : "", w.tmax != null ? `${w.tmax}°` : ""].filter(Boolean).join(" / ");
    const title = [w.summary, w.note].filter(Boolean).join(" · ");
    return `<span class="wx${w.kind === "seasonal" ? " seasonal" : ""}" title="${esc(title)}" aria-label="${esc((withDate ? fmtDate(w.date) + " " : "") + (w.summary || "") + (t ? ", 기온 " + t : "") + (w.pop != null ? `, 강수확률 ${w.pop}%` : "") + (w.kind === "seasonal" ? ", 평년 기준" : ""))}">
      ${withDate ? `<span class="wx-d">${esc(fmtDate(w.date).replace(/^\d+월 /, ""))}</span>` : ""}<span class="wx-i" aria-hidden="true">${esc(w.icon || "")}</span><span class="wx-t">${esc(t)}</span>${w.pop != null ? `<span class="wx-p">☂ ${esc(w.pop)}%</span>` : ""}${w.kind === "seasonal" ? `<small>평년</small>` : ""}</span>`;
  }
  function weatherStrip(plan) {
    const W = plan.weather || [];
    if (!W.length) return "";
    const seasonal = W.some((w) => w.kind === "seasonal");
    const note = (W.find((w) => w.note) || {}).note;
    return `<div class="wx-strip" aria-label="날씨"><span class="wx-k">날씨</span>${W.map((w) => wxChip(w, true)).join("")}${seasonal && note ? `<span class="wx-note">${esc(note)}</span>` : ""}</div>`;
  }

  /* ───────── 교통 안내 카드 ───────── */
  const LEG_TYPE = { access: "이동", ride: "탑승", egress: "도착 후", checkin: "수속", drive: "운전" };
  const isDrive = (g) => !!g && (g.mode === "자차" || (g.legs || []).some((l) => l.type === "drive"));
  function transportGuide(g) {
    if (!g) return "";
    if (isDrive(g)) return driveGuide(g);
    const legs = (g.legs || []).map((l, i) => `<li class="tg-leg t-${esc(l.type || "ride")}"><span class="tg-ty">${esc(LEG_TYPE[l.type] || l.type || "")}</span>
      <b>${esc(l.from || "")}${l.to ? ` → ${esc(l.to)}` : ""}</b>${l.minutes != null ? `<span class="tg-min">약 ${fmtMin(l.minutes)}</span>` : ""}${l.note ? `<small>${esc(l.note)}</small>` : ""}</li>`).join("");
    const opts = (g.options || []).length ? `<div class="mm-scroll tg-scroll"><table class="tbl tg-opts"><thead><tr><th scope="col">경로</th><th scope="col">역·공항까지</th><th scope="col">탑승</th><th scope="col">합계</th></tr></thead><tbody>
      ${g.options.map((o) => `<tr${o.best ? ' class="best"' : ""}><th scope="row">${esc(o.name)}${o.best ? ` <span class="badge ok">가장 빨라요</span>` : ""}</th><td class="num-c">${o.access_min != null ? fmtMin(o.access_min) : "-"}</td><td class="num-c">${o.ride_min != null ? fmtMin(o.ride_min) : "-"}</td><td class="num-c"><b>${o.total_min != null ? fmtMin(o.total_min) : "-"}</b></td></tr>`).join("")}</tbody></table></div>` : "";
    const book = (g.booking || []).filter((b) => /^https:\/\//i.test(b.url || "")).map((b) => `<a class="btn sm ghost" href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.label)} ${ico("ico-pop")}</a>`).join("");
    const fuel = (g.fuel || []).length ? `<div class="tg-fuel"><p class="tg-fuel-h"><span aria-hidden="true">⛽</span> 주유 안내</p><ul>${g.fuel.map((f) => `<li><b>${esc(f.at)}</b> ${esc(f.text)}</li>`).join("")}</ul></div>` : "";
    return `<section class="tguide" aria-labelledby="tg-h">
      <div class="tg-head"><h3 id="tg-h">어떻게 가나요?</h3><span class="badge route">${esc(g.mode || "")}</span>${g.total_min != null ? `<span class="tg-total">편도 약 <b>${fmtMin(g.total_min)}</b>${g.estimated !== false ? " (추정)" : ""}</span>` : ""}</div>
      ${g.summary ? `<p class="tg-sum">${esc(g.summary)}</p>` : ""}
      ${legs ? `<ol class="tg-legs">${legs}</ol>` : ""}
      ${opts}
      ${book ? `<div class="tg-book"><div class="tg-book-b">${book}</div><p class="small muted">예약은 해당 사이트에서 직접 해 주세요. 모모는 예약·결제를 대신하지 않아요.</p></div>` : ""}
      ${(g.tips || []).length ? `<ul class="tg-tips">${g.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
      ${fuel}</section>`;
  }

  /* 자차: 도로 경로 요약이 주인공, 열차는 '참고'로 접어 둔다 */
  function driveGuide(g) {
    const d = (g.legs || []).find((l) => l.type === "drive") || {};
    const alts = g.alternatives || [];
    const altTbl = alts.length ? `<details class="tg-alt"><summary>참고: 열차로 가면</summary><div class="mm-scroll tg-scroll"><table class="tbl tg-opts"><thead><tr><th scope="col">경로</th><th scope="col">역까지</th><th scope="col">탑승</th><th scope="col">합계</th></tr></thead><tbody>
      ${alts.map((o) => `<tr><th scope="row">${esc(o.name)}</th><td class="num-c">${o.access_min != null ? fmtMin(o.access_min) : "-"}</td><td class="num-c">${o.ride_min != null ? fmtMin(o.ride_min) : "-"}</td><td class="num-c">${o.total_min != null ? fmtMin(o.total_min) : "-"}</td></tr>`).join("")}</tbody></table></div></details>` : "";
    const fuel = (g.fuel || []).length ? `<div class="tg-fuel"><p class="tg-fuel-h"><span aria-hidden="true">⛽</span> 주유 안내</p><ul>${g.fuel.map((f) => `<li><b>${esc(f.at)}</b> ${esc(f.text)}</li>`).join("")}</ul></div>` : "";
    const hasRoute = Array.isArray(g.route_path) && g.route_path.length > 1;
    return `<section class="tguide drive" aria-labelledby="tg-h">
      <div class="tg-head"><h3 id="tg-h">어떻게 가나요?</h3><span class="badge route">자차</span>${g.total_min != null ? `<span class="tg-total">편도 약 <b>${fmtMin(g.total_min)}</b>${g.estimated !== false ? " (추정)" : ""}</span>` : ""}</div>
      <div class="tg-drive">
        <div class="tg-drive-ico" aria-hidden="true">${ico("ico-car")}</div>
        <div class="tg-drive-b">
          <p class="tg-drive-route"><b>${esc(d.from || "")}</b> → <b>${esc(d.to || "")}</b></p>
          <p class="tg-drive-nums">${d.distance_km != null ? `<span><b>${esc(Math.round(d.distance_km))}</b> km</span>` : ""}${d.minutes != null ? `<span>약 <b>${fmtMin(d.minutes)}</b></span>` : ""}</p>
          ${g.summary ? `<p class="tg-sum">${esc(g.summary)}</p>` : ""}
          ${g.traffic_note ? `<p class="tg-traffic">${esc(g.traffic_note)}</p>` : ""}
          ${d.note || g.source ? `<p class="tg-src">${esc([...new Set([d.note, g.source].filter(Boolean))].join(" · "))}</p>` : ""}
        </div>
      </div>
      <div class="tg-actions noprint">
        ${hasRoute ? `<button type="button" class="btn sm ghost" data-act="route-fit">${ico("ico-pin")}지도에서 가는 길 보기</button>` : ""}
        <button type="button" class="btn sm ghost" data-act="cctv">${ico("ico-cam")}경로 CCTV 보기</button>
      </div>
      <div id="cctv-box" class="cctv-box" hidden></div>
      ${fuel}
      ${(g.tips || []).length ? `<ul class="tg-tips">${g.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
      ${altTbl}</section>`;
  }

  /* ───────── 경로 CCTV (국가교통정보센터) ───────── */
  const HLS_SRC = "https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.13/hls.min.js";
  let hlsLoading = null;
  const loadHls = () => hlsLoading || (hlsLoading = new Promise((res, rej) => {
    if (window.Hls) return res(window.Hls);
    const s = document.createElement("script"); s.src = HLS_SRC; s.crossOrigin = "anonymous";
    s.onload = () => res(window.Hls); s.onerror = () => { hlsLoading = null; rej(new Error("hls")); };
    document.head.appendChild(s);
  }));
  async function loadCctv() {
    const box = $("#cctv-box");
    if (!box) return;
    box.hidden = false;
    box.innerHTML = `<p class="muted small">CCTV 목록을 불러오는 중이에요…</p>`;
    let j = null;
    if (DEMO) j = { available: false, message: "데모 모드에서는 CCTV를 불러오지 않아요. 실제 상담 화면에서 국가교통정보센터 CCTV를 볼 수 있어요." };
    else { try { const r = await fetch("/traffic/cctv?session_id=" + encodeURIComponent(state.sessionId || "")); j = await r.json(); } catch (e) { j = null; } }
    if (!j || !j.available || !(j.items || []).length) {
      state.cctv = [];
      box.innerHTML = `<p class="cctv-none">${esc((j && j.message) || "지금은 경로 CCTV를 불러올 수 없어요.")}</p>`;
      showCctvMarkers();
      return;
    }
    state.cctv = j.items;
    box.innerHTML = `<p class="cctv-h">경로 CCTV <span class="muted">${esc(j.items.length)}곳 · 출처: ${esc(j.source || "국가교통정보센터")}</span></p>
      <ul class="cctv-grid">${j.items.map((c, i) => `<li><button type="button" class="cctv-c" data-act="cctv-play" data-i="${i}">${ico("ico-cam")}<span class="cctv-n">${esc(c.name)}</span>${c.road ? `<span class="badge ${c.road === "고속도로" ? "user" : "draft"}">${esc(c.road)}</span>` : ""}</button></li>`).join("")}</ul>`;
    showCctvMarkers();
  }
  function showCctvMarkers() {
    if (!M.map || !window.L) return;
    if (M.cctv) { M.map.removeLayer(M.cctv); M.cctv = null; }
    const list = (state.cctv || []).filter((c) => c.lat != null && c.lng != null);
    if (!list.length) return;
    M.cctv = L.layerGroup();
    list.forEach((c) => {
      const i = state.cctv.indexOf(c);
      L.marker([Number(c.lat), Number(c.lng)], { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk cam">${ico("ico-cam")}</span>`, iconSize: [22, 22], iconAnchor: [11, 11] }), title: `CCTV ${c.name}`, alt: `CCTV ${c.name}` })
        .on("click", () => playCctv(i)).addTo(M.cctv);
    });
    M.cctv.addTo(M.map);
  }
  let hlsInst = null;
  function cctvDialog() {
    let d = $("#cctv-dlg");
    if (!d) {
      d = document.createElement("dialog");
      d.id = "cctv-dlg"; d.className = "cctv-dlg"; d.setAttribute("aria-labelledby", "cctv-t");
      document.body.appendChild(d);
      d.addEventListener("close", () => { if (hlsInst) { hlsInst.destroy(); hlsInst = null; } const v = $("video", d); if (v) { v.pause(); v.removeAttribute("src"); } });
    }
    return d;
  }
  async function playCctv(i) {
    const c = (state.cctv || [])[i];
    if (!c) return;
    const d = cctvDialog();
    const safeUrl = /^https?:\/\//i.test(c.url || "") ? c.url : "";
    d.innerHTML = `<div class="cctv-dh"><h2 id="cctv-t">${esc(c.name)}</h2>${c.road ? `<span class="badge draft">${esc(c.road)}</span>` : ""}<button type="button" class="x" data-act="cctv-close" aria-label="닫기">×</button></div>
      <div class="cctv-v"><video muted autoplay playsinline controls aria-label="${esc(c.name)} CCTV 영상"></video><p class="cctv-err" hidden>영상을 바로 재생하지 못했어요. ${safeUrl ? `<a href="${esc(safeUrl)}" target="_blank" rel="noopener">새 창에서 보기</a>` : ""}</p></div>
      <p class="cctv-src">영상 출처: 국가교통정보센터(ITS) · 실시간 영상은 몇 초 늦을 수 있어요</p>`;
    if (d.showModal) d.showModal(); else d.setAttribute("open", "");
    const v = $("video", d), err = $(".cctv-err", d);
    const fail = () => { err.hidden = false; };
    if (!safeUrl) { fail(); return; }
    v.addEventListener("error", fail);
    const isHls = /\.m3u8(\?|$)/i.test(safeUrl) || /hls/i.test(c.format || "");
    if (isHls && !v.canPlayType("application/vnd.apple.mpegurl")) {
      try {
        const Hls = await loadHls();
        if (!Hls || !Hls.isSupported()) return fail();
        hlsInst = new Hls();
        hlsInst.on(Hls.Events.ERROR, (_, data) => { if (data && data.fatal) fail(); });
        hlsInst.loadSource(safeUrl); hlsInst.attachMedia(v);
      } catch (e) { fail(); }
    } else v.src = safeUrl;
    const p = v.play && v.play(); if (p && p.catch) p.catch(() => {});
  }

  /* ───────── 준비물 체크리스트 ───────── */
  function packItems(P) {
    if (!P) return [];
    return [].concat(P.common || [], ...(P.by_day || []).map((d) => d.items || []), P.custom || []);
  }
  function packRecount(P) { const all = packItems(P); P.done = all.filter((x) => x.checked).length; P.total = all.length; }
  function packItem(it, custom) {
    const id = `pk-${esc(it.id)}`;
    return `<li class="pk-item${it.checked ? " done" : ""}"><input type="checkbox" id="${id}" data-pk="${esc(it.id)}"${it.checked ? " checked" : ""}>
      <label for="${id}"><span class="pk-l">${esc(it.label)}</span>${it.category ? `<span class="pk-cat">${esc(it.category)}</span>` : ""}${it.reason ? `<small>${esc(it.reason)}</small>` : ""}</label>
      ${custom ? `<button type="button" class="del noprint" data-act="pk-del" data-id="${esc(it.id)}" aria-label="${esc(it.label)} 빼기">×</button>` : ""}</li>`;
  }
  function packingSection(plan) {
    const P = plan.packing;
    if (!P) return "";
    const days = plan.itinerary || [];
    const customs = P.custom || [];
    const done = P.done != null ? P.done : packItems(P).filter((x) => x.checked).length;
    const total = P.total != null ? P.total : packItems(P).length;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const groups = [];
    if ((P.common || []).length || customs.some((c) => c.day == null)) {
      groups.push(`<div class="pk-g"><h4>공통</h4><ul>${(P.common || []).map((x) => packItem(x)).join("")}${customs.filter((c) => c.day == null).map((x) => packItem(x, true)).join("")}</ul></div>`);
    }
    (P.by_day || []).forEach((g) => {
      const mine = customs.filter((c) => Number(c.day) === Number(g.day));
      groups.push(`<div class="pk-g" style="--day:${dayColor(g.day)}"><h4><span class="pk-day">${esc(g.day)}일차</span>${fmtDate(g.date)} ${wxChip(weatherOf(g.date))}</h4>
        <ul>${(g.items || []).map((x) => packItem(x)).join("")}${mine.map((x) => packItem(x, true)).join("")}</ul></div>`);
    });
    customs.filter((c) => c.day != null && !(P.by_day || []).some((g) => Number(g.day) === Number(c.day))).forEach((c) => groups.push(`<div class="pk-g"><h4>${esc(c.day)}일차</h4><ul>${packItem(c, true)}</ul></div>`));
    return `<section class="packing" id="packing" aria-labelledby="pk-h">
      <div class="pk-head"><h3 id="pk-h">여행 준비 체크리스트</h3><span class="pk-count"><b>${done}</b>/${total}</span></div>
      <div class="b-track pk-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="준비물 ${done}/${total}"><span style="width:${pct}%"></span></div>
      <div class="pk-groups">${groups.join("")}</div>
      <form class="pk-add noprint" data-form="pk-add" aria-label="준비물 추가"><label class="sr" for="pk-in">준비물</label><input id="pk-in" name="label" type="text" maxlength="30" placeholder="더 챙길 것 (예: 보조배터리)" required autocomplete="off">
        <label class="sr" for="pk-day">날짜</label><select id="pk-day" name="day" class="sel"><option value="">공통</option>${days.map((d) => `<option value="${esc(d.day)}">${esc(d.day)}일차</option>`).join("")}</select>
        <button class="btn sm ghost">추가</button></form></section>`;
  }
  function renderPackPanel() {
    const el = $("#pack-panel");
    if (!el) return;
    const P = state.plan && state.plan.packing;
    if (!P) { el.hidden = true; el.innerHTML = ""; return; }
    el.hidden = false;
    const all = packItems(P);
    const done = all.filter((x) => x.checked).length, total = all.length;
    const left = all.filter((x) => !x.checked);
    const wasOpen = !!el.querySelector("details[open]");
    el.innerHTML = `<details${wasOpen ? " open" : ""}><summary class="pk-sum"><span class="ph-title">준비물</span><b>${done}/${total}</b>
        <span class="b-track pk-mini" aria-hidden="true"><span style="width:${total ? Math.round((done / total) * 100) : 0}%"></span></span><span class="muted">${left.length ? `남은 것 ${left.length}개` : "다 챙겼어요"}</span></summary>
      <ul class="pk-quick">${left.slice(0, 10).map((x) => `<li class="pk-item"><input type="checkbox" id="pq-${esc(x.id)}" data-pk="${esc(x.id)}"><label for="pq-${esc(x.id)}"><span class="pk-l">${esc(x.label)}</span>${x.category ? `<span class="pk-cat">${esc(x.category)}</span>` : ""}</label></li>`).join("")}</ul>
      <a class="small" href="#packing" data-act="pk-goto">체크리스트 전체 보기</a></details>`;
  }
  function renderPacking() {
    const sec = $("#packing");
    if (sec && state.plan) { const tmp = document.createElement("div"); tmp.innerHTML = packingSection(state.plan); if (tmp.firstElementChild) sec.replaceWith(tmp.firstElementChild); else sec.remove(); }
    renderPackPanel();
  }
  async function packOp(op, desc) {
    const P = state.plan && state.plan.packing;
    if (!P) return;
    const snap = JSON.stringify(P);
    // 낙관적 반영
    if (op.op === "toggle") { const it = packItems(P).find((x) => String(x.id) === String(op.id)); if (it) it.checked = op.checked; }
    if (op.op === "remove") P.custom = (P.custom || []).filter((x) => String(x.id) !== String(op.id));
    if (op.op === "add" && DEMO) (P.custom = P.custom || []).push({ id: "local-" + Date.now(), label: op.label, checked: false, day: op.day });
    packRecount(P);
    renderPacking();
    if (DEMO) return;
    try {
      let packing = null;
      if (READ_ONLY && !VIEW) {
        const r = await fetch(`/plan/${encodeURIComponent(BOOT.planId)}/packing`, { method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify(op) });
        const j = await r.json().catch(() => null);
        if (!r.ok || !j || !j.packing) throw new Error();
        packing = j.packing;
      } else {
        const j = await callApi({ question: `[준비물] ${desc}`, action: "pack", pack: op });
        if (!j || j.status === "오류" || !j.plan || !j.plan.packing) throw new Error(j && j.answer);
        if (j.draft_version) state.draftVersion = j.draft_version;
        packing = j.plan.packing;
      }
      state.plan.packing = packing;
      renderPacking();
    } catch (e) {
      state.plan.packing = JSON.parse(snap);
      renderPacking();
      toast((e && e.message) || "준비물을 저장하지 못했어요. 잠시 후 다시 해 주세요.");
    }
  }
  function onPackChange(e) {
    const t = e.target;
    if (t.classList && t.classList.contains("ps-far-day")) { const b = t.parentNode.querySelector('[data-act="ps-add-far"]'); if (b) b.textContent = t.value ? `포함할게요(${t.value}일차)` : "포함할게요"; return; }
    if (!t.dataset || !t.dataset.pk) return;
    const it = packItems(state.plan && state.plan.packing).find((x) => String(x.id) === t.dataset.pk);
    packOp({ op: "toggle", id: t.dataset.pk, checked: t.checked }, `${it ? it.label : t.dataset.pk} ${t.checked ? "챙김" : "해제"}`);
  }

  /* 전체 화면 주소 */
  function fullViewUrl() {
    if (DEMO) return "/?demo=1&view=1";
    if (state.planStatus === "확정" && state.exports && state.exports.share_url) return state.exports.share_url;
    return state.sessionId ? `/view/${encodeURIComponent(state.sessionId)}` : "";
  }
  async function shareLink(url) {
    const abs = new URL(url || location.href, location.href).href;
    if (navigator.share) { try { await navigator.share({ title: document.title, url: abs }); return; } catch (e) { if (e && e.name === "AbortError") return; } }
    try { await navigator.clipboard.writeText(abs); toast("링크를 복사했어요"); } catch (e) { toast("복사하지 못했어요. 주소창의 링크를 복사해 주세요."); }
  }

  function sendCost(cost, desc) { send({ question: `[비용 ${cost.op === "add" ? "추가" : "빼기"}] ${desc}`, action: "cost", cost }, "cost"); }

  /* ───────── 여행 후 정산 (공유 페이지) ───────── */
  async function expApi(method, path, body) {
    try {
      const r = await fetch(`/plan/${encodeURIComponent(BOOT.planId)}/expenses${path || ""}`, {
        method, headers: body ? { "Content-Type": "application/json; charset=utf-8" } : {}, body: body ? JSON.stringify(body) : undefined,
      });
      const j = await r.json().catch(() => null);
      if (!r.ok || !j) throw new Error((j && (j.error || j.answer)) || "정산 정보를 불러오지 못했어요.");
      return j;
    } catch (e) { toast(e.message || "정산 정보를 불러오지 못했어요."); return null; }
  }
  async function loadExpenses() {
    const j = await expApi("GET");
    if (j) { state.expenses = j; renderExpenses(); }
    else { state.expenses = { error: true }; renderExpenses(); }
  }
  function renderExpenses() {
    const host = $("#expenses");
    if (!host || !READ_ONLY || VIEW || !state.plan) return;
    const E = state.expenses;
    const days = state.plan.itinerary || [];
    const cats = (E && Array.isArray(E.categories) && E.categories.length) ? E.categories : Array.from(new Set(COST_CATS.concat(((state.plan.costs || {}).items || []).map((i) => i.category))));
    const reportLink = `<a class="btn sm ghost" href="/plan/${esc(BOOT.planId)}/report" target="_blank" rel="noopener">여행 결과 리포트 보기</a>`;
    let body;
    if (!E) body = `<p class="muted">정산 정보를 불러오는 중이에요…</p>`;
    else if (E.error) body = `<p class="muted">정산 정보를 아직 불러올 수 없어요. 잠시 후 새로고침해 주세요.</p>`;
    else {
      const S = E.summary || {};
      const actual = E.actual || [];
      const list = actual.length ? `<ul class="exp-list">${actual.map((x) => `<li><span class="d">${fmtDate(x.date)}</span><span class="cat">${esc(x.category)}</span><span class="l">${esc(x.label)}${x.memo ? `<small>${esc(x.memo)}</small>` : ""}</span><b>${won(x.amount)}</b>
          <button type="button" class="btn xs ghost noprint" data-act="exp-del" data-id="${esc(x.id)}" aria-label="${esc(x.label)} 지출 삭제">삭제</button></li>`).join("")}</ul>` : `<p class="muted">아직 적은 지출이 없어요. 여행하면서 쓴 돈을 적어 보세요.</p>`;
      const rows = (S.by_category || []).map((r) => `<tr><th scope="row">${esc(r.category)}</th><td class="num-c">${won(r.planned)}</td><td class="num-c">${won(r.actual)}</td><td class="num-c ${r.diff > 0 ? "neg" : "pos"}">${signedWon(r.diff)}</td></tr>`).join("");
      const diffT = (S.total_actual || 0) - (S.total_planned || 0);
      const budgetRow = S.budget ? `<p class="exp-budget ${S.total_actual > S.budget ? "over" : ""}">최대 예산 ${won(S.budget)} 중 <b>${Math.round((S.total_actual / S.budget) * 100)}%</b> 사용${S.total_actual > S.budget ? ` · <b>${won(S.total_actual - S.budget)}</b> 초과` : ""}</p>` : "";
      body = `${list}
        <table class="tbl cmp"><thead><tr><th scope="col">분류</th><th scope="col">계획</th><th scope="col">실제</th><th scope="col">차이</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="4" class="muted">비교할 항목이 아직 없어요.</td></tr>`}</tbody>
        <tfoot><tr><th scope="row">합계</th><td class="num-c">${won(S.total_planned)}</td><td class="num-c">${won(S.total_actual)}</td><td class="num-c ${diffT > 0 ? "neg" : "pos"}">${signedWon(diffT)}</td></tr>
        ${S.per_person_actual != null ? `<tr><th scope="row">1인당 실제</th><td></td><td class="num-c">${won(S.per_person_actual)}</td><td></td></tr>` : ""}</tfoot></table>${budgetRow}`;
    }
    const form = `<form class="exp-form noprint" data-form="exp" aria-label="지출 적기">
      <div class="f"><label for="ex-date">날짜</label><select id="ex-date" name="date" class="sel">${days.map((d) => `<option value="${esc(d.date)}">${fmtDate(d.date)}</option>`).join("")}</select></div>
      <div class="f"><label for="ex-cat">분류</label><select id="ex-cat" name="category" class="sel">${cats.map((c) => `<option>${esc(optLabel(c))}</option>`).join("")}</select></div>
      <div class="f grow"><label for="ex-label">내용</label><input id="ex-label" name="label" type="text" maxlength="60" placeholder="예) 남녘들밥상 점심" required></div>
      <div class="f"><label for="ex-amt">금액(원)</label><input id="ex-amt" name="amount" type="number" inputmode="numeric" min="0" step="100" placeholder="48000" required></div>
      <div class="f grow"><label for="ex-memo">메모</label><input id="ex-memo" name="memo" type="text" maxlength="80" placeholder="선택"></div>
      <button class="btn sm primary">지출 적기</button></form>`;
    host.hidden = false;
    host.innerHTML = `<div class="airmail thin" aria-hidden="true"></div><h3 id="exp-h">여행 경비 정산 <span class="muted">계획 대비 실제</span></h3>${form}${body}<p class="exp-foot">${reportLink}</p>`;
  }

  /* ───────── 오른쪽 패널: 합계·확정·내보내기 ───────── */
  function renderActions() {
    const el = $("#act-panel");
    if (!el) return;
    const plan = state.plan;
    if (!plan) { el.innerHTML = ""; el.hidden = true; return; }
    el.hidden = false;
    const local = (plan.totals && plan.totals.local) || {};
    const ld = (plan.totals && plan.totals.long_distance) || {};
    const ldMin = (ld.outbound ? ld.outbound.duration_min || 0 : 0) + (ld.inbound ? ld.inbound.duration_min || 0 : 0);
    const ldMode = (ld.outbound || ld.inbound || {}).mode;
    // 이동 합계는 지도 범례 위 한 줄로 (정보창 높이 절약)
    const ms = $("#map-sum");
    if (ms) ms.innerHTML = [local.distance_km != null ? `현지 이동 <b>${esc(km(local.distance_km))}</b> · 약 <b>${fmtMin(local.travel_min)}</b>` : "",
      ldMin ? `${esc(ldMode || "장거리")} 왕복 약 <b>${fmtMin(ldMin)}</b>` : ""].filter(Boolean).join(" · ") + (local.distance_km != null || ldMin ? " (추정)" : "");
    const totals = "";
    let act = "";
    if (VIEW) {
      const v = state.view || {};
      const pid = v.plan_id;
      const sid = encodeURIComponent(v.session_id || state.sessionId || "");
      const draft = !pid;
      const htmlUrl = DEMO_VIEW ? "#" : pid ? `/plan/${encodeURIComponent(pid)}.html` : `/view/${sid}.html`;
      act = `<p class="view-badge ${draft ? "draft" : "ok"}"><i class="dot ${draft ? "warn" : "ok"}"></i>${draft ? "초안 · 확정 전" : "확정된 계획"}</p>
        <nav class="exports" aria-label="내려받기·공유">
          <a class="btn sm primary" href="${esc(htmlUrl)}" download>HTML 내려받기</a>
          <button type="button" class="btn sm ghost" data-act="share-link">링크 공유</button>
          <button type="button" class="btn sm ghost" data-act="print">인쇄·PDF</button>
          ${pid ? `<a class="btn sm ghost" href="/plan/${encodeURIComponent(pid)}.docx" download>Word(.docx)</a><a class="btn sm ghost" href="/plan/${encodeURIComponent(pid)}.ics" download>캘린더(.ics)</a>` : ""}
        </nav>${draft ? `<p class="muted small">아직 확정 전 초안이에요. 상담 화면에서 확정하면 공유 링크가 고정돼요.</p>` : ""}`;
    } else if (READ_ONLY) {
      const id = esc(BOOT.planId);
      act = `<nav class="exports" aria-label="공유"><button type="button" class="btn sm ghost" data-act="share-link">링크 공유</button></nav>` + exportLinks({ html_url: `/plan/${id}.html`, docx_url: `/plan/${id}.docx`, print_url: `/plan/${id}?print=1`, ics_url: `/plan/${id}.ics`, report_url: `/plan/${id}/report` });
    } else if (state.planStatus === "확정" && state.exports) {
      act = `<p class="state ok"><i class="dot ok"></i>확정됐어요. 원하는 형식으로 가져가세요.</p>${exportLinks(state.exports)}`;
    } else if (state.planStatus === "승인 대기") {
      const pg = getProgress();
      const full = Number(pg.percent) >= 100;
      const na = (pg.assumed || []).length, nm = (pg.missing || []).length;
      const left = [na ? `가정 ${na}개` : "", nm ? `미정 ${nm}개` : ""].filter(Boolean).join("·") || "확정하지 않은 조건";
      const np = pendingCount();
      if (np) act = `<p class="approve-note pend">조건을 바꿨어요 — 다시 세운 뒤 확정할 수 있어요.</p>
        <button type="button" class="btn primary wide" data-act="cond-apply"${state.busy ? " disabled" : ""}>변경 ${np}건 · 계획 다시 세우기</button>
        <button type="button" class="btn primary wide ghostish" disabled aria-disabled="true">지금 조건 그대로 확정할게요</button>`;
      else act = `${full ? "" : `<p class="approve-note">${esc(left)}가 남아 있어요 · 그대로 확정해도 괜찮아요</p>`}
        <button type="button" class="btn primary wide" data-act="approve"${state.busy ? " disabled" : ""}>${full ? "이 계획으로 확정할게요" : "지금 조건 그대로 확정할게요"}</button>
        <p class="muted small">확정하면 공유 링크·HTML·Word·PDF·캘린더를 만들어요. 확정 전에는 저장·공유하지 않아요.</p>`;
    }
    const fv = !READ_ONLY && fullViewUrl();
    el.innerHTML = totals + act + (fv ? `<a class="fv-link" href="${esc(fv)}" target="_blank" rel="noopener">${ico("ico-pop")}전체 화면으로 보기</a>` : "");
  }
  function exportLinks(x) {
    const items = [
      ["share_url", "공유 링크", ' target="_blank" rel="noopener"'],
      ["html_url", "HTML", " download"],
      ["docx_url", "Word(.docx)", " download"],
      ["print_url", "PDF로 저장", ' target="_blank" rel="noopener"'],
      ["ics_url", "캘린더(.ics)", " download"],
      ["report_url", "여행 결과 리포트", ' target="_blank" rel="noopener"'],
    ].filter((i) => x[i[0]] && !(READ_ONLY && i[0] === "share_url"));
    const copy = x.share_url && !READ_ONLY ? `<button type="button" class="btn xs ghost" data-act="copy" data-url="${esc(x.share_url)}">링크 복사</button>` : "";
    return `<nav class="exports" aria-label="내보내기">${items.map((i) => `<a class="btn sm ${i[0] === "share_url" ? "primary" : "ghost"}" href="${esc(x[i[0]])}"${i[2]}>${i[1]}</a>`).join("")}${copy}</nav>`;
  }

  function renderSide() {
    renderConditions();
    renderCosts();
    renderPackPanel();
    renderActions();
    const empty = $("#map-empty");
    if (empty) empty.hidden = !!(state.plan && (state.plan.itinerary || []).length);
  }

  /* ───────── 지도 ───────── */
  const M = { map: null, groups: {}, markers: {}, all: null, needsFit: false };
  function ensureMap() {
    if (!window.L) return null;
    if (M.map) return M.map;
    M.map = L.map("map", { zoomControl: true, scrollWheelZoom: true, attributionControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" }).addTo(M.map);
    M.map.setView([36.3, 127.8], 7);
    return M.map;
  }
  function coordOf(s, P) {
    if (s.lat != null && s.lng != null) return [Number(s.lat), Number(s.lng)];
    const p = s.place_id != null ? P.get(String(s.place_id)) : null;
    return p && p.lat != null ? [Number(p.lat), Number(p.lng)] : null;
  }
  function renderMap() {
    const plan = state.plan;
    const tabs = $("#map-tabs");
    if (!plan || !(plan.itinerary || []).length) { if (tabs) tabs.innerHTML = ""; return; }
    if (!window.L) { const e = $("#map-empty"); if (e) { e.hidden = false; e.querySelector("p").textContent = "지도를 불러오지 못했어요. 인터넷 연결을 확인해 주세요."; } return; }
    const map = ensureMap();
    Object.values(M.groups).forEach((g) => map.removeLayer(g));
    M.groups = {}; M.markers = {};
    const P = placeIndex(plan);
    const allPts = [];
    plan.itinerary.forEach((d) => {
      const color = dayColor(d.day);
      const g = L.layerGroup();
      const pts = [];
      (d.stops || []).forEach((s, i) => {
        const c = coordOf(s, P);
        const leg = findLeg(d, s);
        if (i === 0 && leg && Array.isArray(leg.path) && leg.path.length > 1) {
          const st = leg.path[0];
          pts.push(st);
          L.marker(st, { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk start" style="--c:${color}"></span>`, iconSize: [14, 14], iconAnchor: [7, 7] }), title: leg.from_name || "출발", keyboard: false }).addTo(g);
        }
        if (!c) return;
        if (pts.length && leg && leg.duration_min) {
          const a = pts[pts.length - 1];
          L.marker([(a[0] + c[0]) / 2, (a[1] + c[1]) / 2], { icon: L.divIcon({ className: "leg-lbl-wrap", html: `<span class="leg-lbl" style="--c:${color}">약 ${fmtMin(leg.duration_min)}</span>`, iconSize: null }), interactive: false, keyboard: false }).addTo(g);
        }
        pts.push(c);
        const order = s.order != null ? s.order : i + 1;
        const key = `${d.day}-${order}`;
        const kind = s.type === "meal" ? "meal" : s.type === "stay" ? "stay" : "place";
        const inner = kind === "meal" ? ico("ico-fork") : kind === "stay" ? "숙" : esc(order);
        const size = kind === "meal" ? 22 : 28;
        const m = L.marker(c, {
          icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk ${kind}" style="--c:${color}">${inner}</span>`, iconSize: [size, size], iconAnchor: [size / 2, size / 2] }),
          title: `${d.day}일차 ${order}. ${s.name}`, alt: `${d.day}일차 ${order}. ${s.name}`, riseOnHover: true,
        }).addTo(g);
        m.on("click", () => focusCard(`stop-${key}`));
        M.markers[key] = m;
      });
      if (pts.length > 1) L.polyline(pts, { color, weight: 3, opacity: 0.85, dashArray: "6 7", interactive: false }).addTo(g);
      allPts.push(...pts);
      M.groups[d.day] = g;
    });
    M.all = allPts.length ? L.latLngBounds(allPts) : null;
    // 자차 도로 경로(OSRM): 실선. '전체' 화면 맞춤에는 넣지 않고 '가는 길 보기'로 따로 맞춘다
    if (M.route) { map.removeLayer(M.route); M.route = null; M.routeBounds = null; }
    const tg = plan.transport_guide;
    if (isDrive(tg) && Array.isArray(tg.route_path) && tg.route_path.length > 1) {
      const rp = tg.route_path.map((q) => [Number(q[0]), Number(q[1])]).filter((q) => isFinite(q[0]) && isFinite(q[1]));
      M.route = L.layerGroup();
      L.polyline(rp, { color: dayColor(1), weight: 3, opacity: 0.6, interactive: false }).addTo(M.route);
      L.marker(rp[0], { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk home" style="--c:${dayColor(1)}">${ico("ico-home")}</span>`, iconSize: [24, 24], iconAnchor: [12, 12] }), title: "출발지", alt: "출발지" }).addTo(M.route);
      M.routeBounds = L.latLngBounds(rp);
    } else state.routeFit = false;
    const lg = $("#map-panel .legend");
    if (lg) lg.textContent = "점선은 방문 순서(직선)예요. 실제 도로와 다를 수 있어요." + (M.route ? " 실선은 자차 도로 경로(OSRM)예요." : "");
    if (state.mapDay !== "all" && !M.groups[state.mapDay]) state.mapDay = "all";
    if (tabs) {
      tabs.innerHTML = [["all", "전체"]].concat(plan.itinerary.map((d) => [String(d.day), `${d.day}일차`])).map(([v, t]) =>
        `<button type="button" role="tab" class="tab${String(state.mapDay) === v && !state.routeFit ? " on" : ""}" aria-selected="${String(state.mapDay) === v && !state.routeFit}" data-act="mapday" data-v="${v}"${v !== "all" ? ` style="--c:${dayColor(v)}"` : ""}>${v !== "all" ? `<i class="dot" style="background:${dayColor(v)}"></i>` : ""}${t}</button>`).join("")
        + (M.route ? `<button type="button" class="tab route-tab${state.routeFit ? " on" : ""}" aria-pressed="${!!state.routeFit}" data-act="route-fit">${ico("ico-car")}가는 길 보기</button>` : "");
    }
    applyMapDay();
  }
  function applyMapDay() {
    if (!M.map) return;
    if (M.route) { if (state.mapDay === "all" || String(state.mapDay) === "1" || state.routeFit) M.route.addTo(M.map); else M.map.removeLayer(M.route); }
    if (state.routeFit && M.routeBounds) { fitMap(M.routeBounds); return; }
    let b = null;
    Object.entries(M.groups).forEach(([day, g]) => {
      const show = state.mapDay === "all" || String(state.mapDay) === day;
      if (show) { g.addTo(M.map); const pts = []; g.eachLayer((l) => { if (l.getLatLng) pts.push(l.getLatLng()); }); if (pts.length) b = b ? b.extend(L.latLngBounds(pts)) : L.latLngBounds(pts); }
      else M.map.removeLayer(g);
    });
    fitMap(b);
  }
  function fitMap(b) {
    if (!M.map) return;
    const el = M.map.getContainer();
    if (!el.clientHeight || !el.clientWidth) { M.needsFit = true; return; }
    M.map.invalidateSize();
    const bounds = b || M.all;
    if (bounds && bounds.isValid()) M.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 14, animate: false });
    M.needsFit = false;
  }
  function refitMap() {
    if (!M.map) return;
    M.map.invalidateSize();
    if (M.needsFit) applyMapDay();
  }
  let activeKey = null;
  /* 장소 검색 결과를 회색 임시 마커로 */
  function showSearchMarkers() {
    if (!M.map || !window.L) return;
    if (M.search) { M.map.removeLayer(M.search); M.search = null; }
    const list = (state.search.results || []).concat(state.search.farDismissed ? [] : (state.search.far || [])).filter((r) => r.lat != null && r.lng != null);
    if (!list.length) return;
    M.search = L.layerGroup();
    list.forEach((r, i) => {
      L.marker([Number(r.lat), Number(r.lng)], {
        icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk search">${i + 1}</span>`, iconSize: [22, 22], iconAnchor: [11, 11] }),
        title: `검색 결과 ${i + 1}. ${r.name}`, alt: `검색 결과 ${i + 1}. ${r.name}`,
      }).addTo(M.search);
    });
    M.search.addTo(M.map);
  }

  function highlightMarker(key, pan) {
    if (activeKey && M.markers[activeKey]) { const e = M.markers[activeKey].getElement(); if (e) e.classList.remove("active"); }
    activeKey = key;
    const m = M.markers[key];
    if (!m) return;
    const e = m.getElement();
    if (e) e.classList.add("active");
    m.setZIndexOffset(500);
    if (pan && M.map && M.map.hasLayer(m)) {
      const inner = M.map.getBounds().pad(-0.18);
      if (!inner.contains(m.getLatLng())) M.map.panTo(m.getLatLng(), { animate: !REDUCED.matches, duration: 0.5 });
    }
  }
  function focusCard(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1800);
  }
  let io = null;
  function observeStops() {
    if (io) io.disconnect();
    if (!("IntersectionObserver" in window)) return;
    const scroller = $("#scroller");
    const root = scroller && MQ_PC.matches && getComputedStyle(scroller).overflowY !== "visible" ? scroller : null;
    io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        const key = en.target.id.replace(/^stop-/, "");
        highlightMarker(key, true);
      });
    }, { root, rootMargin: "-40% 0px -50% 0px", threshold: 0 });
    $$("#brochure .stop").forEach((s) => io.observe(s));
  }

  /* ───────── 배치: PC 오른쪽 패널 ↔ 모바일 접이식 ───────── */
  function stashPanels() {
    const side = $("#side");
    if (!side) return;
    ["#cond-panel", "#cost-panel", "#pack-panel", "#map-panel", "#act-panel"].forEach((s) => { const x = $(s); if (x && x.parentNode !== side) side.appendChild(x); });
  }
  function placePanels() {
    const pc = MQ_PC.matches;
    const side = $("#side");
    const cond = $("#cond-panel"), mapP = $("#map-panel"), cost = $("#cost-panel"), act = $("#act-panel"), pack = $("#pack-panel");
    if (!side) return;
    if (VIEW) {
      // 전체 화면: 내려받기 줄 → 큰 지도 → 조건·경비·준비물 → 브로셔 (1단)
      let slot = $("#vf-slot");
      const br = $("#brochure");
      if (!slot && br) { slot = document.createElement("div"); slot.id = "vf-slot"; slot.className = "vf-slot"; br.parentNode.insertBefore(slot, br); }
      if (slot) {
        let row = $("#vf-row");
        if (!row) { row = document.createElement("div"); row.id = "vf-row"; row.className = "vf-row"; }
        [act, mapP].forEach((x) => { if (x) slot.appendChild(x); });
        slot.appendChild(row);
        [cond, cost, pack].forEach((x) => { if (x) row.appendChild(x); });
      }
    } else if (pc) {
      stashPanels();
    } else {
      const mc = $("#m-cond-slot"), mm = $("#m-map-body"), ma = $("#m-act-slot");
      if (cond && mc && cond.parentNode !== mc) mc.appendChild(cond);
      if (mapP && mm && mapP.parentNode !== mm) mm.appendChild(mapP);
      if (ma) { if (cost && cost.parentNode !== ma) ma.appendChild(cost); if (pack && pack.parentNode !== ma) ma.appendChild(pack); if (act && act.parentNode !== ma) ma.appendChild(act); }
    }
    setTimeout(refitMap, 60);
  }

  /* ───────── 내 여행 계획 (브라우저 저장: session_id 목록만) ───────── */
  const Trips = {
    KEY: "momo.trips", CUR: "momo.current",
    list() { try { const a = JSON.parse(localStorage.getItem(this.KEY) || "[]"); return Array.isArray(a) ? a.filter((x) => typeof x === "string") : []; } catch (e) { return []; } },
    save(a) { try { localStorage.setItem(this.KEY, JSON.stringify(a.slice(0, 30))); } catch (e) { /* 저장 불가 환경 */ } },
    remember(sid) { this.save([sid].concat(this.list().filter((x) => x !== sid))); },
    forget(sid) { this.save(this.list().filter((x) => x !== sid)); if (this.current() === sid) this.setCurrent(null); },
    current() { try { return localStorage.getItem(this.CUR); } catch (e) { return null; } },
    setCurrent(sid) { try { if (sid) localStorage.setItem(this.CUR, sid); else localStorage.removeItem(this.CUR); } catch (e) { /* 무시 */ } },
  };

  function fmtUpdated(s) {
    const d = new Date(s);
    if (isNaN(d)) return "";
    const diff = (Date.now() - d.getTime()) / 60000;
    if (diff < 1) return "방금 전";
    if (diff < 60) return `${Math.floor(diff)}분 전`;
    if (diff < 60 * 24) return `${Math.floor(diff / 60)}시간 전`;
    return `${d.getMonth() + 1}월 ${d.getDate()}일 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  async function openTrips() {
    const dlg = $("#trips");
    if (!dlg) return;
    const body = $("#trips-body", dlg);
    body.innerHTML = `<p class="muted">불러오는 중이에요…</p>`;
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
    const ids = Trips.list();
    if (!ids.length) { body.innerHTML = tripsEmpty(); return; }
    let trips = [];
    try {
      const r = await fetch("/trips?ids=" + encodeURIComponent(ids.join(",")));
      const j = await r.json();
      trips = (j && j.trips) || [];
    } catch (e) { body.innerHTML = `<p class="muted">목록을 불러오지 못했어요. 잠시 후 다시 열어 주세요.</p>`; return; }
    if (!trips.length) { body.innerHTML = tripsEmpty(); return; }
    body.innerHTML = `<ul class="trip-list">${trips.map((t) => {
      const done = t.status === "확정";
      const pct = Math.max(0, Math.min(100, Number(t.progress) || 0));
      const cur = t.session_id === state.sessionId;
      return `<li class="trip-item${cur ? " cur" : ""}">
        <div class="ti-top"><span class="badge ${done ? "ok" : "draft"}">${done ? "확정" : "초안"}</span>${cur ? `<span class="badge user">지금 보는 계획</span>` : ""}<span class="ti-time">${esc(fmtUpdated(t.updated))}</span></div>
        <h3>${esc(t.title || t.region || "이름 없는 여행")}</h3>
        <p class="muted">${t.start_date ? fmtDate(t.start_date, true) : "날짜 미정"}${t.nights != null ? ` · ${t.nights ? `${esc(t.nights)}박 ${Number(t.nights) + 1}일` : "당일치기"}` : ""}</p>
        ${t.progress != null ? `<div class="mini-prog${pct >= 100 ? " done" : ""}" aria-label="조건 확정 ${pct}%"><span style="width:${pct}%"></span></div><p class="small muted">조건 확정 ${pct}%</p>` : ""}
        <div class="ti-act"><button type="button" class="btn sm primary" data-act="trip-load" data-sid="${esc(t.session_id)}">불러오기</button>
        <button type="button" class="btn sm ghost" data-act="trip-forget" data-sid="${esc(t.session_id)}">목록에서 빼기</button></div></li>`;
    }).join("")}</ul><p class="small muted">이 목록은 이 브라우저에만 보관돼요. 다른 기기에서는 보이지 않아요.</p>`;
  }
  const tripsEmpty = () => `<div class="trips-empty">${bird("bird big")}<p>아직 저장된 계획이 없어요.<br>모모와 상담해서 계획서를 받으면 여기에 자동으로 모여요.</p></div>`;
  function closeTrips() { const d = $("#trips"); if (d) { if (d.close) d.close(); else d.removeAttribute("open"); } }

  function resetSession() {
    const thread = $("#thread"), br = $("#brochure");
    Object.assign(state, { sessionId: null, draftVersion: null, status: null, stage: "intake", planStatus: null, plan: null, conditions: null, exports: null, openCond: null, mapDay: "all", progress: null });
    stashPanels();
    if (thread) Array.from(thread.children).forEach((c) => { if (c !== br) c.remove(); });
    if (br) { br.innerHTML = ""; br.hidden = true; }
    if (M.map) Object.values(M.groups).forEach((g) => M.map.removeLayer(g));
    M.groups = {}; M.markers = {};
    const tabs = $("#map-tabs"); if (tabs) tabs.innerHTML = "";
    placePanels();
    document.documentElement.dataset.season = "winter";
    resetStarter();
    welcome();
    renderSide();
    setMood("idle");
  }

  async function loadTrip(sid, auto) {
    let j = null;
    try { const r = await fetch("/trips/" + encodeURIComponent(sid)); if (r.ok) j = await r.json(); } catch (e) { j = null; }
    if (!j || !j.session_id) {
      if (auto) { Trips.setCurrent(null); return; }
      toast("그 계획을 찾지 못했어요. 목록에서 빼 둘게요."); Trips.forget(sid); openTrips(); return;
    }
    resetSession();
    S.collapsed = true;
    bubble("momo", auto ? "지난번에 보던 계획을 다시 펼쳐 놨어요." : "저장된 계획을 불러왔어요.");
    const st = j.status === "초안" ? "승인 대기" : j.status;
    handleResponse(Object.assign({}, j, { status: st, quick_replies: [] }), "chat");
    if (j.progress) { state.progress = j.progress; updateProgress(); renderActions(); }
  }

  /* ───────── 이벤트(위임) ───────── */
  function onClick(e) {
    const pop = $("#tp-pop");
    if (pop && !pop.hidden && !e.target.closest("#tp-pop") && !e.target.closest(".tp-trigger") && !e.target.closest("#tp-back")) tpClose(false);
    const t = e.target.closest("[data-act]");
    if (!t) return;
    const act = t.dataset.act;
    if (/^tp-/.test(act)) { tpClick(t, act); return; }
    if (/^(st-|cal-)/.test(act)) { if (!t.disabled) starterClick(t, act); return; }
    switch (act) {
      case "qr": if (!READ_ONLY) send({ question: t.dataset.q }, "chat", t.dataset.q); break;
      case "cond": {
        const k = t.dataset.key;
        state.openCond = state.openCond === k ? null : k;
        if (state.openCond === "arrival") ARR.key = null; // 열 때마다 현재 값(변경 예정 포함)으로 초안을 새로
        renderConditions();
        if (state.openCond) { const tr = $(`#ed-${cssEsc(k)} .ed-time .tp-trigger`); if (tr) { tpOpen(tr); break; } }
        const ed = document.getElementById("ed-" + k);
        if (ed) { const f = ed.querySelector("input, button.chip"); if (f) f.focus(); }
        else { const b = $(`.pill-b[data-key="${cssEsc(k)}"]`); if (b) b.focus(); }
        break;
      }
      case "cond-set": sendSet(t.dataset.key, t.dataset.v); break;
      case "cond-sugg": {
        const inp = t.closest(".cond-ed").querySelector("input");
        if (t.dataset.key === "interests" && inp.value.trim()) { if (inp.value.indexOf(t.dataset.v) < 0) inp.value = inp.value.trim() + ", " + t.dataset.v; }
        else inp.value = t.dataset.v;
        inp.focus();
        break;
      }
      case "more": { const s = t.closest(".stop"); setOpen(s, !s.classList.contains("open")); break; }
      case "thumb": {
        const fig = t.closest(".media");
        const img = $(".main-img img", fig);
        img.src = t.dataset.src;
        $("figcaption", fig).textContent = t.dataset.cap;
        fig.classList.toggle("contain", t.dataset.l3 === "1");
        $$(".thumb", fig).forEach((x) => x.classList.toggle("on", x === t));
        break;
      }
      case "up": case "down": {
        if (!canEdit()) return;
        const name = t.closest(".stop").querySelector("h4").textContent;
        sendEdit({ op: "reorder", place_id: t.dataset.pid, day: Number(t.dataset.day), delta: act === "up" ? -1 : 1 }, `${name} 순서를 ${act === "up" ? "앞으로" : "뒤로"}`);
        break;
      }
      case "move": {
        if (!canEdit()) return;
        const sel = t.parentNode.querySelector("select");
        const name = t.closest(".stop").querySelector("h4").textContent;
        sendEdit({ op: "move", place_id: t.dataset.pid, day: Number(sel.value) }, `${name}을(를) ${sel.value}일차로 옮기기`);
        break;
      }
      case "remove": {
        if (!canEdit()) return;
        if (!t.classList.contains("confirm")) {
          t.classList.add("confirm"); t.textContent = "한 번 더 누르면 빼요";
          setTimeout(() => { if (t.isConnected) { t.classList.remove("confirm"); t.textContent = "빼기"; } }, 3500);
          return;
        }
        sendEdit({ op: "remove", place_id: t.dataset.pid, day: Number(t.dataset.day) }, `${t.dataset.name} 빼기`);
        break;
      }
      case "add": {
        if (!canEdit()) return;
        const sel = t.parentNode.querySelector("select");
        const edit = { op: "add", place_id: t.dataset.pid };
        if (sel && sel.value) edit.day = Number(sel.value);
        sendEdit(edit, `${t.dataset.name} 일정에 넣기${edit.day ? ` (${edit.day}일차)` : ""}`);
        break;
      }
      case "approve":
        if (DEMO) { toast("데모 모드에서는 바뀌지 않아요"); return; }
        send({ question: "", action: "approve" }, "approve", "이 계획으로 확정할게요");
        break;
      case "copy":
        if (navigator.clipboard) navigator.clipboard.writeText(new URL(t.dataset.url, location.href).href).then(() => toast("공유 링크를 복사했어요"), () => toast("복사하지 못했어요. 링크를 길게 눌러 복사해 주세요."));
        break;
      case "mapday": state.mapDay = t.dataset.v === "all" ? "all" : Number(t.dataset.v); state.routeFit = false; renderMap(); break;
      case "route-fit": {
        state.routeFit = t.closest("#map-tabs") ? !state.routeFit : true;
        if (!MQ_PC.matches && !state.mobileMapOpen) { const mb = $('[data-act="m-map"]'); if (mb) mb.click(); }
        renderMap();
        if (!t.closest("#map-tabs")) { const mp = $("#map"); if (mp) mp.scrollIntoView({ behavior: scrollBehavior(), block: "center" }); }
        break;
      }
      case "cctv": loadCctv(); break;
      case "cctv-play": playCctv(Number(t.dataset.i)); break;
      case "cctv-close": { const dl = $("#cctv-dlg"); if (dl) { if (dl.close) dl.close(); else dl.removeAttribute("open"); } break; }
      case "m-cond": {
        const open = t.getAttribute("aria-expanded") !== "true";
        t.setAttribute("aria-expanded", String(open));
        $("#m-cond-slot").hidden = !open;
        break;
      }
      case "m-map": {
        state.mobileMapOpen = !state.mobileMapOpen;
        t.setAttribute("aria-expanded", String(state.mobileMapOpen));
        $("#m-map-body").hidden = !state.mobileMapOpen;
        if (state.mobileMapOpen) setTimeout(() => { M.needsFit = true; refitMap(); }, 30);
        break;
      }
      case "pop-demo": { e.preventDefault(); const m = t.closest(".msg"); if (m && m._html) openAnswerWindow(m._html); break; }
      case "pk-del": packOp({ op: "remove", id: t.dataset.id }, "항목 빼기"); break;
      case "pk-goto": { e.preventDefault(); const sec = $("#packing"); if (sec) sec.scrollIntoView({ behavior: scrollBehavior(), block: "start" }); break; }
      case "share-link": shareLink(VIEW || READ_ONLY ? location.href.split("?")[0] : fullViewUrl()); break;
      case "print": if (DEMO_VIEW) { document.documentElement.classList.add("print-mode"); window.print(); } else location.href = location.pathname + "?print=1"; break;
      case "cost-del": sendCost({ op: "remove", id: t.dataset.id }, t.dataset.name || ""); break;
      case "man-toggle": { const was = t.getAttribute("aria-expanded") === "true"; state.manual.open = !was; state.manual.closed = was; paintManual(); if (!was) { const n = $('#man-form [data-man="name"]'); if (n) n.focus(); } break; }
      case "man-pick": startMapPick(); break;
      case "ps-far-no": state.search.farDismissed = true; paintSearch(); break;
      case "ps-add-far": {
        if (!canEdit()) return;
        const r = (state.search.far || [])[Number(t.dataset.i)];
        if (!r) return;
        const sel = t.parentNode.querySelector("select");
        const edit = { op: "add", candidate: r };
        if (sel && sel.value) edit.day = Number(sel.value);
        state.search = { q: "", results: [], loading: false };
        showSearchMarkers();
        sendEdit(edit, `${r.name} 추가(먼 곳)${edit.day ? ` (${edit.day}일차)` : ""}`);
        break;
      }
      case "ps-add": {
        if (!canEdit()) return;
        const r = (state.search.results || [])[Number(t.dataset.i)];
        if (!r) return;
        const sel = t.parentNode.querySelector("select");
        const edit = { op: "add", candidate: r };
        if (sel && sel.value) edit.day = Number(sel.value);
        state.search = { q: "", results: [], loading: false };
        showSearchMarkers();
        sendEdit(edit, `${r.name} 추가${edit.day ? ` (${edit.day}일차)` : ""}`);
        break;
      }
      case "ps-name": {
        if (!canEdit()) return;
        const name = t.dataset.q;
        state.search = { q: "", results: [], loading: false };
        showSearchMarkers();
        sendEdit({ op: "add", name }, `${name} 추가`);
        break;
      }
      case "cond-revert": { delete state.pending[t.dataset.key]; renderConditions(); renderActions(); break; }
      case "cond-apply": applyPending(); break;
      case "cond-clear": state.pending = {}; renderConditions(); renderActions(); break;
      case "geo-pick": pickGeo(t); break;
      case "trips-open": openTrips(); break;
      case "trips-close": closeTrips(); break;
      case "trip-load": closeTrips(); if (!state.busy) loadTrip(t.dataset.sid, false); break;
      case "trip-forget": Trips.forget(t.dataset.sid); t.closest(".trip-item").remove(); if (!$(".trip-item")) $("#trips-body").innerHTML = tripsEmpty(); toast("목록에서 뺐어요. 서버의 계획은 그대로예요."); break;
      case "new-chat": {
        if (state.busy) return;
        closeTrips(); Trips.setCurrent(null); resetSession();
        const q = $("#q"); if (q) q.focus();
        toast("새 여행 상담을 시작해요");
        break;
      }
      case "exp-del": {
        (async () => { const j = await expApi("DELETE", "/" + encodeURIComponent(t.dataset.id)); if (j) { state.expenses = j; renderExpenses(); toast("지출을 지웠어요"); } })();
        break;
      }
    }
  }

  function onSubmit(e) {
    const f = e.target.closest("form[data-form]");
    if (!f) return;
    e.preventDefault();
    const kind = f.dataset.form;
    if (kind === "set") {
      const inp = f.querySelector("input");
      if (inp.getAttribute("aria-expanded") === "true" && Geo.active >= 0) return; // 자동완성 선택 중
      const v = inp.value.trim();
      if (v) sendSet(f.dataset.key, v);
    } else if (kind === "place-search") {
      placeSearch(f.querySelector("input").value, true);
    } else if (kind === "add-name") {
      if (!canEdit()) return;
      const name = f.querySelector("input").value.trim();
      if (!name) return;
      const day = f.querySelector("select").value;
      const edit = { op: "add", name };
      if (day) edit.day = Number(day);
      sendEdit(edit, `${name} 추가${day ? ` (${day}일차)` : ""}`);
    } else if (kind === "manual") {
      if (!canEdit()) return;
      if (Geo.items.length && Geo.active >= 0) return;
      const Mn = state.manual;
      const name = String(f.querySelector('[name="name"]').value || "").trim();
      const address = String(f.querySelector('[name="address"]').value || "").trim();
      if (!name) return;
      const manual = { name };
      if (address) manual.address = address;
      if (Mn.lat != null) { manual.lat = Number(Mn.lat); manual.lng = Number(Mn.lng); }
      const day = f.querySelector("select").value;
      const edit = { op: "add", manual };
      if (day) edit.day = Number(day);
      state.manual = { open: false, name: "", address: "", lat: null, lng: null, src: null, picking: false, place: null };
      showManualPin();
      sendEdit(edit, `${name} 직접 추가${day ? ` (${day}일차)` : ""}`);
    } else if (kind === "arrival") {
      if (Geo.items.length && Geo.active >= 0) return;
      arrSubmit(f);
    } else if (kind === "pk-add") {
      const fd = new FormData(f);
      const label = String(fd.get("label") || "").trim();
      if (!label) return;
      const day = fd.get("day") ? Number(fd.get("day")) : null;
      packOp({ op: "add", label, day }, `${label} 추가`);
      f.reset();
    } else if (kind === "cost-add") {
      const fd = new FormData(f);
      const label = String(fd.get("label") || "").trim();
      const amount = Math.round(Number(fd.get("amount")));
      if (!label || !(amount >= 0)) return;
      sendCost({ op: "add", category: String(fd.get("category")), label, amount }, `${fd.get("category")} · ${label} ${won(amount)}`);
    } else if (kind === "exp") {
      const fd = new FormData(f);
      const amount = Number(fd.get("amount"));
      if (!fd.get("label") || !(amount >= 0)) return;
      const btn = f.querySelector("button");
      btn.disabled = true;
      expApi("POST", "", { date: fd.get("date"), category: fd.get("category"), label: String(fd.get("label")).trim(), amount: Math.round(amount), memo: String(fd.get("memo") || "").trim() })
        .then((j) => { btn.disabled = false; if (j) { state.expenses = j; renderExpenses(); toast("지출을 적었어요"); const n = $("#ex-label"); if (n) n.focus(); } });
    }
  }

  /* ───────── 입력창 ───────── */
  function initComposer() {
    const form = $("#composer");
    if (!form) return;
    const q = $("#q");
    const grow = () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 140) + "px"; };
    q.addEventListener("input", grow);
    q.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = q.value.trim();
      if (!v || state.busy) return;
      q.value = ""; grow();
      const note = $("#mic-note");
      if (note && !note.dataset.sticky) note.hidden = true;
      send({ question: v }, "chat", v);
    });
  }

  /* ───────── 음성 입력 (Web Speech API, ko-KR, 자동 전송 없음) ───────── */
  function initVoice() {
    const mic = $("#mic"), note = $("#mic-note"), q = $("#q");
    if (!mic || !q) return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      note.textContent = "이 브라우저는 음성 인식을 지원하지 않아요. 휴대폰 키보드의 마이크(받아쓰기)를 써 주세요.";
      note.hidden = false; note.dataset.sticky = "1";
      return;
    }
    mic.hidden = false;
    let rec = null, base = "";
    mic.onclick = () => {
      if (rec) { rec.stop(); return; }
      rec = new SR();
      rec.lang = "ko-KR"; rec.interimResults = true; rec.continuous = false;
      base = q.value ? q.value.trim() + " " : "";
      rec.onresult = (e) => {
        let text = "";
        for (const r of e.results) text += r[0].transcript;
        q.value = base + text;
        q.dispatchEvent(new Event("input"));
      };
      rec.onerror = (e) => {
        note.textContent = e.error === "not-allowed" || e.error === "service-not-allowed"
          ? "마이크 권한이 꺼져 있어요. 브라우저 설정에서 마이크를 허용해 주세요(휴대폰은 https 주소에서만 돼요)."
          : "잘 못 들었어요. 마이크를 다시 눌러 말씀해 주세요.";
        note.hidden = false;
      };
      rec.onend = () => {
        rec = null; mic.classList.remove("on"); mic.setAttribute("aria-pressed", "false");
        if (q.value.trim()) { note.textContent = "받아 적은 내용을 확인하고 '보내기'를 눌러 주세요."; note.hidden = false; q.focus(); }
      };
      mic.classList.add("on"); mic.setAttribute("aria-pressed", "true");
      note.textContent = "듣고 있어요… 말씀이 끝나면 자동으로 멈춰요. (음성은 브라우저의 음성 인식 서비스에서 글자로 바뀌어요)";
      note.hidden = false;
      rec.start();
    };
  }

  /* ───────── 옛 공유 데이터(plan 없음) → plan 모양으로 ───────── */
  function legacyToPlan(p) {
    const items = p.itinerary || [];
    const dates = Array.from(new Set(items.map((i) => i.date)));
    const places = [];
    const itinerary = dates.map((date, di) => {
      const stops = items.filter((i) => i.date === date && i.type !== "move").map((it, k) => {
        const id = `legacy-${di}-${k}`;
        places.push({ id, name: it.place_name, category: TYPE_LABEL[it.type] || "", in_itinerary: true, summary: it.summary, reason: it.reason, hours: it.hours_text, source: it.source,
          images: it.image_url ? [{ url: it.image_url, license: it.image_license, credit: "한국관광공사" }] : [] });
        return { order: k + 1, place_id: id, name: it.place_name, type: it.type, arrive: it.start_time, depart: it.end_time, hours_status: it.hours_status, reason: it.reason };
      });
      return { day: di + 1, date, stops, legs: [], day_travel_min: items.filter((i) => i.date === date).reduce((a, i) => a + (i.travel_minutes_from_prev || 0), 0) };
    });
    return { trip: { title: p.title, start_date: p.start, regions: [p.region_label], origin: p.departure_sido, nights: p.nights }, places, itinerary, totals: {}, warnings: [] };
  }

  /* ───────── 인쇄 모드 ───────── */
  function printWhenReady() {
    const imgs = $$("#brochure img");
    const loaded = Promise.all(imgs.map((im) => (im.complete ? Promise.resolve() : new Promise((r) => { im.addEventListener("load", r, { once: true }); im.addEventListener("error", r, { once: true }); }))));
    Promise.race([loaded, new Promise((r) => setTimeout(r, 1500))]).then(() => setTimeout(() => window.print(), 150));
  }

  /* ───────── PC 폭 조절 막대 ───────── */
  const SPLIT_DEFAULT = 62;
  function initSplit() {
    const bar = $("#split");
    if (!bar) return;
    const root = document.documentElement;
    const clamp = (pct) => {
      const w = window.innerWidth || 1280;
      const min = (420 / w) * 100, max = 100 - ((340 + 12) / w) * 100;
      return Math.max(min, Math.min(max, pct));
    };
    let cur = SPLIT_DEFAULT;
    try { const v = parseFloat(localStorage.getItem("momo.split")); if (isFinite(v)) cur = v; } catch (e) { /* 무시 */ }
    let t = 0;
    const apply = (pct, save) => {
      cur = clamp(pct);
      root.style.setProperty("--split", cur.toFixed(2) + "%");
      bar.setAttribute("aria-valuenow", String(Math.round(cur)));
      bar.setAttribute("aria-valuetext", `왼쪽 ${Math.round(cur)}%`);
      clearTimeout(t); t = setTimeout(() => { if (M.map) M.map.invalidateSize(); }, 120);
      if (save) { try { localStorage.setItem("momo.split", cur.toFixed(2)); } catch (e) { /* 무시 */ } }
    };
    apply(cur, false);
    let dragging = false;
    bar.addEventListener("pointerdown", (e) => { dragging = true; bar.setPointerCapture(e.pointerId); bar.classList.add("drag"); document.body.classList.add("resizing"); e.preventDefault(); });
    bar.addEventListener("pointermove", (e) => { if (dragging) apply((e.clientX / window.innerWidth) * 100, false); });
    const end = () => { if (!dragging) return; dragging = false; bar.classList.remove("drag"); document.body.classList.remove("resizing"); apply(cur, true); };
    bar.addEventListener("pointerup", end);
    bar.addEventListener("pointercancel", end);
    bar.addEventListener("dblclick", () => apply(SPLIT_DEFAULT, true));
    bar.addEventListener("keydown", (e) => {
      const map = { ArrowLeft: cur - 2, ArrowRight: cur + 2, Home: 0, End: 100 };
      if (e.key in map) { e.preventDefault(); apply(map[e.key], true); }
      else if (e.key === "Enter") { e.preventDefault(); apply(SPLIT_DEFAULT, true); }
    });
    window.addEventListener("resize", () => apply(cur, false));
  }

  /* 읽기 전용(공유 /plan/{id} · 전체 화면 /view/{sid} · 데모 전체 화면) */
  function initReadOnly(payload) {
    state.plan = payload.plan || legacyToPlan(payload);
    state.view = payload;
    state.planStatus = VIEW && !payload.plan_id ? "승인 대기" : "확정";
    if (payload.session_id) state.sessionId = payload.session_id;
    if (payload.draft_version) state.draftVersion = payload.draft_version;
    state.conditions = (state.plan.trip && state.plan.trip.checklist) || null;
    state.progress = (state.plan.trip && state.plan.trip.progress) || null;
    document.body.classList.add("read-only");
    if (VIEW) document.body.classList.add("view-full");
    if (PRINT) document.documentElement.classList.add("print-mode");
    if (state.plan.trip && state.plan.trip.start_date) document.documentElement.dataset.season = seasonOf(state.plan.trip.start_date);
    const desk = $("#desk");
    if (desk) { desk.innerHTML = DESK_SVG; setMood("idle"); }
    { const ml = $("#mood-line"); if (ml) ml.textContent = "계획서를 펼쳐 놨어요"; }
    if (DEMO_VIEW) { const h = $(".desk-title h1"); if (h) h.textContent = (state.plan.trip && state.plan.trip.title) || "여행 계획서"; const s = $(".desk-sub"); if (s) s.textContent = "모모의 여행 상담소 · 전체 화면 보기 (데모)"; }
    renderBrochure({});
    renderSide();
    if (!VIEW) loadExpenses();
    if (PRINT) printWhenReady();
  }

  /* ───────── 시작 ───────── */
  function init() {
    document.body.insertAdjacentHTML("afterbegin", SPRITE);
    if (BRAND_SPRITE !== "momo-bird") $$('use[href="#momo-bird"]').forEach((u) => u.setAttribute("href", "#" + BRAND_SPRITE));
    document.documentElement.dataset.season = document.documentElement.dataset.season || "winter";
    document.addEventListener("click", onClick);
    document.addEventListener("submit", onSubmit);
    document.addEventListener("error", (e) => {
      const im = e.target;
      if (!(im instanceof HTMLImageElement) || im.dataset.failed) return;
      im.dataset.failed = "1";
      const fig = im.closest("figure");
      if (fig && !im.closest(".thumb")) { fig.classList.add("none"); const box = im.closest(".main-img") || im; box.outerHTML = MOMO_ILLUST; const c = fig.querySelector("figcaption"); if (c) c.textContent = "사진을 불러오지 못했어요 · 모모 그림"; }
      else if (im.closest(".thumb")) im.closest(".thumb").remove();
    }, true);
    const onMq = () => { placePanels(); observeStops(); };
    if (MQ_PC.addEventListener) MQ_PC.addEventListener("change", onMq); else MQ_PC.addListener(onMq);
    window.addEventListener("resize", () => { clearTimeout(init._r); init._r = setTimeout(refitMap, 150); tpPlace(); });
    document.addEventListener("scroll", () => { if (TP.trigger) { if (TP.trigger.isConnected) tpPlace(); else tpClose(false); } }, true);

    document.addEventListener("change", onPackChange);
    if (!VIEW) initSplit();
    if (READ_ONLY) {
      if (DEMO_VIEW) {
        fetch("/static/sample_plan.json").then((r) => r.json())
          .then((plan) => initReadOnly({ title: plan.trip && plan.trip.title, plan, view: true, session_id: "demo" }))
          .catch(() => toast("데모 계획서(sample_plan.json)를 불러오지 못했어요."));
      } else initReadOnly(BOOT.payload || {});
      return;
    }

    const desk = $("#desk");
    if (desk) { desk.innerHTML = DESK_SVG; setMood("idle"); }
    initComposer();
    initVoice();
    welcome();
    renderSide();
    placePanels();
    loadOptions();
    document.addEventListener("input", (e) => {
      starterInput(e);
      const t = e.target;
      if (t.dataset && t.dataset.geo) geoOnInput(t);
      if (t.name === "place" && t.closest && t.closest(".arr-form")) ARR.label = t.value;
      if (t.dataset && t.dataset.man) state.manual[t.dataset.man] = t.value;
      if (t.dataset && t.dataset.psearch) placeSearch(t.value);
    });
    document.addEventListener("keydown", geoKeydown);
    document.addEventListener("focusout", (e) => { if (e.target.dataset && e.target.dataset.geo) setTimeout(() => { if (!document.activeElement || !document.activeElement.closest || !document.activeElement.closest("#geo-list")) geoClose(); }, 200); });
    document.addEventListener("mousedown", (e) => { if (e.target.closest("#geo-list")) e.preventDefault(); });
    document.addEventListener("change", starterInput);
    if (!DEMO) { const cur = Trips.current(); if (cur) loadTrip(cur, true); }
    // 데모: ?demo=1 계획서 화면 · ?demo=1&start=1 시작 카드만(제출하면 견본 계획서) · &status=확정 내보내기 화면
    if (DEMO && params.get("start") !== "1") {
      document.documentElement.classList.add("demo");
      Object.assign(S, { region: "suncheon_gurye", from: "2026-12-12", to: "2026-12-13", comp: 3, count: 4, combo: 1 });
      bubble("me", "순천/구례 · 12월 12일(토) ~ 12월 13일(일) · 1박 2일 · 부모님 동반 4명 · KTX·SRT + 렌터카");
      demoPlanResponse(params.get("status") === "확정").then((j) => handleResponse(j, "chat"))
        .catch(() => toast("데모 계획서(sample_plan.json)를 불러오지 못했어요."));
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
