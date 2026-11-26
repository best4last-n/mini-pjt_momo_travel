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
<symbol id="ico-swap" viewBox="0 0 16 16"><path d="M3 5.5h9.5L10 3M13 10.5H3.5L6 13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-layers" viewBox="0 0 16 16"><path d="M8 2 14.5 5.5 8 9 1.5 5.5zM1.5 8.5 8 12l6.5-3.5M1.5 11.2 8 14.7l6.5-3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></symbol>
<symbol id="ico-expand" viewBox="0 0 16 16"><path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-shrink" viewBox="0 0 16 16"><path d="M6 2.5V6H2.5M13.5 6H10V2.5M10 13.5V10h3.5M2.5 10H6v3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-plus" viewBox="0 0 16 16"><path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></symbol>
<symbol id="ico-route" viewBox="0 0 16 16"><circle cx="3.5" cy="3.5" r="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="12.5" cy="12.5" r="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M5.3 3.5h5a2.3 2.3 0 0 1 0 4.6H5.8a2.2 2.2 0 0 0 0 4.4h4.9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></symbol>
<symbol id="ico-grip" viewBox="0 0 16 16"><g fill="currentColor"><circle cx="6" cy="4" r="1.2"/><circle cx="10" cy="4" r="1.2"/><circle cx="6" cy="8" r="1.2"/><circle cx="10" cy="8" r="1.2"/><circle cx="6" cy="12" r="1.2"/><circle cx="10" cy="12" r="1.2"/></g></symbol>
<symbol id="ico-undo" viewBox="0 0 16 16"><path d="M5.5 3.5 2.5 6.5l3 3M2.8 6.5h6.7a4 4 0 0 1 0 8H7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-alert" viewBox="0 0 16 16"><path d="M8 2.2 14.5 13.5h-13z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6.5v3.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="11.6" r=".9" fill="currentColor"/></symbol>
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
    mode: "view", owner: false, needsReconfirm: false, undoCount: 0, planId: null, reconfirming: false,
  };
  // 수정 권한: 상담 화면이거나, /view 에서 이 브라우저가 계획 주인일 때. 실제 편집 컨트롤은 '수정' 모드에서만 보인다
  const editAllowed = () => !!state.plan && !DEMO_VIEW && (!READ_ONLY || !!state.owner);
  const canEdit = () => editAllowed() && state.mode === "edit";
  const modeKey = () => "momo.mode." + (state.sessionId || "new");
  function loadMode() { try { state.mode = localStorage.getItem(modeKey()) === "edit" ? "edit" : "view"; } catch (e) { state.mode = "view"; } }
  function setMode(m) {
    state.mode = m === "edit" ? "edit" : "view";
    try { localStorage.setItem(modeKey(), state.mode); } catch (e) { /* 저장 불가 */ }
    renderBrochure({}); renderSide();
    const b = $(`.mode-seg [data-v="${state.mode}"]`); if (b) b.focus();
  }

  /* ───────── 토스트 ───────── */
  const TOASTS = [];
  function toast(msg, ms, action, opts) {
    const box = $("#toast");
    if (!box) return null;
    opts = opts || {};
    const lines = (Array.isArray(msg) ? msg : [msg]).filter((x) => x != null && x !== "");
    const text = lines.join(" ");
    const progress = Number(ms) >= 30000;
    const kind = opts.kind || (progress ? "progress" : /(못했|실패|끊겼|오류|잃었|불러오지 못)/.test(text) ? "error" : "info");
    // 진행 중 알림과 같은 내용의 알림은 새 알림이 오면 비킨다
    TOASTS.filter((x) => x.kind === "progress" || x.text === text).forEach((x) => closeToast(x, true));
    const dur = kind === "error" || kind === "progress" ? 0 : action ? 6000 : (ms && ms < 3000 ? ms : 4000);
    const el = document.createElement("div");
    el.className = `toast-item k-${kind}`;
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    el.innerHTML = `<div class="toast-msg">${lines.map((m) => `<span>${esc(m)}</span>`).join("")}</div>
      ${action ? `<button type="button" class="toast-act" data-act="${esc(action.act)}">${esc(action.label)}</button>` : ""}
      <button type="button" class="toast-x" data-act="toast-close" aria-label="닫기">×</button>
      ${dur ? `<span class="toast-bar" style="animation-duration:${dur}ms" aria-hidden="true"></span>` : ""}`;
    box.appendChild(el);
    const t = { el, kind, dur, text, left: dur, start: 0, timer: 0, paused: false };
    TOASTS.push(t);
    while (TOASTS.length > 3) closeToast(TOASTS[0], true);
    const pause = () => { if (!t.dur || t.paused) return; clearTimeout(t.timer); t.left -= Date.now() - t.start; t.paused = true; el.classList.add("paused"); };
    const resume = () => { if (!t.dur || !t.paused || el.matches(":hover") || el.contains(document.activeElement)) return; t.paused = false; el.classList.remove("paused"); runToast(t); };
    el.addEventListener("mouseenter", pause); el.addEventListener("focusin", pause);
    el.addEventListener("mouseleave", () => setTimeout(resume, 0)); el.addEventListener("focusout", () => setTimeout(resume, 0));
    if (dur) runToast(t);
    box.classList.add("show");
    return t;
  }
  function runToast(t) { t.start = Date.now(); clearTimeout(t.timer); t.timer = setTimeout(() => closeToast(t), Math.max(200, t.left)); }
  function closeToast(t, instant) {
    const i = TOASTS.indexOf(t);
    if (i < 0) return;
    TOASTS.splice(i, 1);
    clearTimeout(t.timer);
    t.el.classList.add("out");
    setTimeout(() => t.el.remove(), instant ? 0 : 180);
  }
  function closeToastOf(el) { const t = TOASTS.find((x) => x.el === el); if (t) closeToast(t); }

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
  const IN_PLACE = { edit: 1, set: 1, cost: 1, undo: 1, restore: 1 };
  async function send(body, kind, userText) {
    if (state.busy) { toast("모모가 아직 날아가는 중이에요. 잠시만 기다려 주세요."); return; }
    if (DEMO && kind !== "chat") { toast("데모 모드에서는 바뀌지 않아요"); return; }
    if (userText) bubble("me", userText, { time: new Date().toISOString() });
    if (kind === "chat" && body.question) state.lastQuestion = body.question;
    const wait = (kind === "chat" || kind === "approve") ? loadingBubble() : null;
    const useAsync = !DEMO && (body.action === "start" || !!IN_PLACE[kind]);
    setBusy(true, kind);
    setMood("thinking");
    renderStarter();
    let j;
    if (DEMO) {
      await new Promise((r) => setTimeout(r, 900));
      j = body.action === "start" ? await demoPlanResponse(false)
        : (!body.replan && /경주|수원|날짜|박|일정을 다시/.test(body.question || "")) ? await fetch("/static/sample_replan.json").then((r) => r.json()).catch(() => ({ answer: "데모", status: "정보" }))
        : { answer: "데모 모드에서는 모모가 실제로 날아가지 않아요. 브로셔와 지도, 버튼 모양을 둘러봐 주세요!", status: "정보", stage: state.stage, quick_replies: ["알아서 짜 줘"] };
    } else if (useAsync) {
      try { j = await callAsync(body); } finally { jobLine(null); }
    } else {
      j = await callApi(body);
    }
    if (wait) wait.stop();
    setBusy(false, kind);
    handleResponse(j, kind);
    if (useAsync && IN_PLACE[kind] && j && j.plan && j.status !== "오류") {
      const ch = Array.isArray(j.changes) && j.changes.length ? j.changes : ["새 계획으로 바꿨어요"];
      toast(ch, 9000, kind === "edit" && state.undoCount > 0 ? { label: "되돌리기", act: "undo" } : null);
    }
    if (state.focusAfter && j && j.plan) {
      const pid = state.focusAfter; state.focusAfter = null;
      const el = $(`#brochure .stop[data-pid="${cssEsc(pid)}"]`);
      if (el) setTimeout(() => focusCard(el.id), 120);
    }
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
    const newSid = j.session_id && j.session_id !== state.sessionId;
    if (j.session_id) state.sessionId = j.session_id;
    if (newSid) loadMode();
    if ("needs_reconfirm" in j) state.needsReconfirm = !!j.needs_reconfirm;
    if (j.undo_count != null) state.undoCount = Number(j.undo_count) || 0;
    if (j.plan_id) { state.planId = j.plan_id; if (state.view) state.view.plan_id = j.plan_id; }
    // 확정본과 지금 초안이 같으면(확정 상태) 공유 링크를 plan_id 로 만든다
    if (j.plan_id && j.confirmed_version && j.draft_version && j.confirmed_version === j.draft_version && !j.needs_reconfirm && j.status !== "오류") {
      j = Object.assign({}, j, { status: "확정" });
      const id = encodeURIComponent(j.plan_id);
      if (!j.share_url) Object.assign(j, { share_url: `/plan/${id}`, html_url: `/plan/${id}.html`, docx_url: `/plan/${id}.docx`, print_url: `/plan/${id}?print=1`, ics_url: `/plan/${id}.ics`, report_url: `/plan/${id}/report` });
    }
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
    if (j.status === "확정" && state.reconfirming) { state.reconfirming = false; setTimeout(() => toast("다시 확정했어요. 공유 링크는 그대로예요.", 5000), 50); }
    else if (state.reconfirming && kind === "approve") { state.reconfirming = false; setTimeout(() => toast(String(j.answer || "아직 다시 확정할 수 없어요").slice(0, 160), 8000), 50); }
    if (j.status === "확정") {
      state.exports = { share_url: j.share_url, html_url: j.html_url, docx_url: j.docx_url, print_url: j.print_url, ics_url: j.ics_url, report_url: j.report_url };
    }
    const changes = Array.isArray(j.changes) ? j.changes.filter(Boolean) : [];
    const quiet = VIEW || kind === "undo" || kind === "restore";
    const prevNo = state.planNo;
    if (j.plan_no != null) state.planNo = Number(j.plan_no);
    const diff = j.plan_diff || (j.plan && j.plan.diff) || null;
    if (j.plan) { if (diff && JSON.stringify(diff) !== JSON.stringify(state.planDiff)) state.diffHidden = false; state.planDiff = diff; }
    if (kind === "chat" && j.plan && state.replanUpdate) { state.diffForce = true; state.replanUpdate = false; } else if (j.plan && kind !== "restore") state.diffForce = false;
    const b = quiet ? null : bubble("momo", j.answer || "", { plan: state.planNo || 1, replan: j.replan_choice || null, time: new Date().toISOString(), replies: j.quick_replies || j.chips || [], changes, status: j.status, html: j.answer_html || "", longPlan: !!j.plan && (j.stage === "plan" || j.stage === "refine") });
    if (changes.length) toast(changes, 5200);
    if (b) {
      const me = $$("#thread > .msg.me").filter((x) => !x.dataset.plan);
      me.forEach((x) => { x.dataset.plan = b.dataset.plan; });
      if (j.replan_choice) state.replanQ = state.lastQuestion || "";
    }
    if (j.plan) {
      state.plan = j.plan;
      if (j.plan.trip && j.plan.trip.title && !READ_ONLY) document.title = `${j.plan.trip.title} · 모모의 여행 상담소`;
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
    if (!quiet && prevNo != null && state.planNo != null && state.planNo !== prevNo) foldThread();
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

  function fmtMsgTime(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const now = new Date();
    const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    return d.toDateString() === now.toDateString() ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
  }
  function replanCard(rc) {
    return `<div class="replan" role="group" aria-label="다시 계획 방법 고르기">
      ${rc.question ? `<p class="replan-q"><b>${esc(rc.question)}</b></p>` : ""}
      ${(rc.diffs || []).length ? `<ul class="replan-d">${rc.diffs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <div class="replan-b">${(rc.options || [{ key: "update", label: "이 계획을 새 버전으로 고치기" }, { key: "new", label: "새 여행으로 따로 시작하기" }]).map((o, i) =>
        `<button type="button" class="btn ${i === 0 ? "primary" : "ghost"}" data-act="replan" data-k="${esc(o.key)}">${esc(o.label)}</button>`).join("")}</div></div>`;
  }
  function bubble(who, text, opt = {}) {
    const thread = $("#thread");
    if (!thread) return null;
    $$(".replies", thread).forEach((r) => r.remove()); // 지난 빠른 답장은 치운다
    const wrap = document.createElement("div");
    wrap.className = "msg " + who + (opt.status === "오류" ? " is-error" : "");
    const tm = opt.time ? `<time class="msg-t" datetime="${esc(opt.time)}">${esc(fmtMsgTime(opt.time))}</time>` : "";
    if (opt.mid != null) wrap.dataset.mid = String(opt.mid);
    if (opt.plan != null) wrap.dataset.plan = String(opt.plan);
    if (who === "me") {
      wrap.innerHTML = `<div class="bub"><p class="txt">${esc(text)}</p>${tm}</div>`;
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
        <span class="who">모모${opt.status && opt.status !== "오류" ? ` · <em>${esc(opt.status)}</em>` : ""}${tm ? ` · ${tm}` : ""}${pop}</span>
        ${changes}${rich || `${head ? `<p class="txt">${richText(head)}</p>` : ""}${more}`}${opt.replan ? replanCard(opt.replan) : ""}</div>${replies}</div>`;
      if (rich) wrap._html = opt.html;
    }
    thread.appendChild(wrap);
    if (who === "me" && !opt.time) wrap.scrollIntoView({ behavior: scrollBehavior(), block: "end" });
    else if (who === "me" && opt.mid == null) wrap.scrollIntoView({ behavior: scrollBehavior(), block: "end" });
    return wrap;
  }

  function loadingBubble() {
    if (VIEW) return { stop() {} };
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
    const base = (state.conditions && state.conditions.length) ? state.conditions : PLACEHOLDER_CONDS;
    const ml = state.plan && state.plan.meals;
    if (!ml || base.some((c) => c.key === "breakfast")) return base;
    const row = { key: "breakfast", label: "아침 식사", value: ml.breakfast || "근처 식당", status: "confirmed", input: "select",
      options: ml.options && ml.options.length ? ml.options : ["근처 식당", "숙소 조식", "안 먹음"], hint: ml.note || "숙박 다음 날 아침을 어떻게 할지 골라요." };
    const out = base.slice();
    const at = out.findIndex((c) => c.key === "intensity");
    out.splice(at >= 0 ? at : out.length, 0, row);
    return out;
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

  let IN_PLAN = new Set();
  function diffCard() {
    const D = state.planDiff;
    if (!D || state.diffHidden || !(D.big || state.diffForce)) return "";
    const no = state.planNo;
    const chips = [].concat(
      (D.added || []).map((x) => `<span class="dchip add">＋ ${esc(x.name)}${x.day ? ` · ${esc(x.day)}일차` : ""}</span>`),
      (D.moved || []).map((x) => `<span class="dchip mv">${esc(x.name)} ${esc(x.from_day)}→${esc(x.to_day)}일차</span>`)).join("");
    const rm = D.removed || [];
    return `<section class="diff-card" aria-label="바뀐 점">
      <div class="diff-h"><b>${no ? `v${no - 1} → v${no}` : "바뀐 점"}</b><span>${esc(D.summary || "")}</span>
        <button type="button" class="chip sm" data-act="diff-only" aria-pressed="${!!state.diffOnly}">바뀐 곳만 보기</button>
        <button type="button" class="toast-x" data-act="diff-hide" aria-label="바뀐 점 숨기기">×</button></div>
      ${chips ? `<div class="diff-chips">${chips}</div>` : ""}
      ${rm.length ? `<details class="diff-rm"><summary>빠진 곳 ${rm.length}</summary><ul>${rm.map((x) => `<li>${esc(x.name)}${x.day ? ` · ${esc(x.day)}일차` : ""}</li>`).join("")}</ul></details>` : ""}</section>`;
  }
  function statusPill() {
    if (state.needsReconfirm) return `<span class="st-pill edit">수정 중</span>`;
    return state.planStatus === "확정" || ((VIEW || state.planId) && (state.view ? state.view.plan_id : state.planId)) ? `<span class="st-pill ok">확정</span>` : `<span class="st-pill draft">초안</span>`;
  }
  const bannerOff = () => { try { return sessionStorage.getItem("momo.reconfirm.off." + state.sessionId) === "1"; } catch (e) { return !!state.reconfirmOff; } };
  function reconfirmBanner(compact) {
    if (!state.needsReconfirm || !editAllowed()) return "";
    if (bannerOff() && !compact) return "";
    if (bannerOff()) return `<button type="button" class="btn sm primary wide" data-act="reconfirm"${state.busy ? " disabled" : ""}>수정한 계획으로 다시 확정</button>`;
    return `<div class="reconfirm" role="status"><p><b>확정 후 수정됨</b> · 공유 링크에는 확정 당시 내용이 보여요</p>
      <button type="button" class="btn sm primary" data-act="reconfirm"${state.busy ? " disabled" : ""}>다시 확정</button>
      <button type="button" class="toast-x" data-act="reconfirm-off" aria-label="이 안내 닫기">×</button></div>`;
  }
  function modeBar() {
    if (!editAllowed()) return "";
    const ed = state.mode === "edit";
    return `<div class="mode-bar noprint">
      <div class="mode-seg" role="group" aria-label="보기 또는 수정">
        <button type="button" data-act="mode" data-v="view" aria-pressed="${!ed}">보기</button>
        <button type="button" data-act="mode" data-v="edit" aria-pressed="${ed}">${VIEW ? "✏️ 이 계획 고치기" : "수정"}</button></div>
      ${statusPill()}
      ${ed ? `<span class="mode-sp"></span>
        <button type="button" class="btn sm ghost" data-act="undo"${state.undoCount > 0 && !state.busy ? "" : " disabled"} title="직전 수정을 되돌려요(최대 20번)">${ico("ico-undo")}되돌리기${state.undoCount ? ` (${state.undoCount})` : ""}</button>
        <button type="button" class="btn sm primary re-open-btn" data-act="re-open" aria-haspopup="dialog">${ico("ico-route")}동선 편집 · 최적화</button>` : `<span class="mode-hint">${VIEW ? "고치려면 ‘이 계획 고치기’를 눌러 주세요" : "순서·장소를 바꾸려면 ‘수정’을 눌러 주세요"}</span>`}
    </div>${reconfirmBanner()}${ed ? `<p class="mode-tip noprint">${ico("ico-grip")} 카드 왼쪽 손잡이를 끌어 순서를 바꾸거나 다른 날로 옮길 수 있어요. 키보드는 손잡이에서 스페이스 → ↑↓ → 스페이스.</p>` : ""}`;
  }
  function renderBrochure(opt) {
    const el = $("#brochure");
    const plan = state.plan;
    if (!el || !plan) return;
    if (opt && opt.moveAfter && opt.moveAfter.parentNode) opt.moveAfter.after(el);
    stashPanels(); // 모바일에서 브로셔 안으로 옮겨 둔 지도·경비·버튼 패널을 다시 그리기 전에 꺼내 둔다
    const openIds = new Set($$(".stop.open", el).map((s) => s.id));
    document.body.classList.toggle("can-edit", canEdit());
    IN_PLAN = new Set();
    (plan.itinerary || []).forEach((dd) => (dd.stops || []).forEach((st) => { if (st.place_id != null) IN_PLAN.add(String(st.place_id)); }));
    const P = placeIndex(plan);
    const trip = plan.trip || {};
    const days = plan.itinerary || [];
    const ld = (plan.totals && plan.totals.long_distance) || {};
    const lastDay = days.length ? days[days.length - 1].day : null;

    const html = [];
    html.push(tripHeader(plan));
    html.push(modeBar());
    html.push(diffCard());
    html.push(`<div id="m-map-slot" class="m-map"><button type="button" class="m-toggle" data-act="m-map" aria-expanded="${state.mobileMapOpen}" aria-controls="m-map-body">
      ${ico("ico-pin")}<span>지도 보기</span><span class="m-sub">날짜별 동선과 순서</span>${ico("ico-chev", "ico chev")}</button><div id="m-map-body" class="m-body"${state.mobileMapOpen ? "" : " hidden"}></div></div>`);
    const hpts = homePoints(plan);
    if (ld.outbound && !(hpts.st && ld.outbound.mode === "자차")) html.push(ticket(ld.outbound, "가는 길", trip.start_date));
    html.push(transportGuide(plan.transport_guide));
    days.forEach((d) => {
      html.push(daySection(d, P, plan));
      if (d.day === lastDay && ld.inbound && !(hpts.en && ld.inbound.mode === "자차")) html.push(ticket(ld.inbound, "오는 길", d.date));
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

  function titleSuggest(plan) {
    const t = plan.trip || {};
    const d = t.start_date ? new Date(t.start_date + "T00:00:00") : new Date();
    return `${d.getFullYear()} ${SEASON_LABEL[seasonOf(t.start_date)]} ${(t.regions || []).join("·") || ""} 여행`.replace(/\s+/g, " ").trim();
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
        ${state.titleEdit ? `<form class="title-ed noprint" data-form="title"><label class="sr" for="title-in">여행 제목</label>
            <input id="title-in" type="text" maxlength="40" value="${esc(t.title_custom ? t.title : "")}" placeholder="${esc(titleSuggest(plan))}" autocomplete="off">
            <button class="btn sm primary">저장</button><button type="button" class="btn sm ghost" data-act="title-cancel">취소</button>
            ${t.title_custom ? `<button type="button" class="linkish" data-act="title-default">기본 제목으로${t.default_title ? `(${esc(t.default_title)})` : ""}</button>` : ""}</form>`
          : `<h2 id="trip-h" class="display">${esc(t.title || "여행 계획서")}${editAllowed() ? ` <button type="button" class="title-pen noprint" data-act="title-edit" aria-label="여행 제목 바꾸기" title="여행 제목 바꾸기">✎</button>` : ""}</h2>`}
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

  /* 출발지·도착지(집): 서버 start_point/end_point, 없으면 trip.origin + 장거리 이동으로 만든다 */
  function homePoints(plan) {
    const its = plan.itinerary || [];
    const t = plan.trip || {};
    const ld = (plan.totals && plan.totals.long_distance) || {};
    const op = t.origin_point || null, ep = t.end_point || t.origin_point || null;
    let st = its.length ? its[0].start_point : null;
    let en = its.length ? its[its.length - 1].end_point : null;
    if (!st && (op || t.origin) && ld.outbound) st = { kind: "home", role: "출발", name: (op && op.name) || t.origin, label: (op && op.label) || t.origin, time: ld.outbound.depart, leg: ld.outbound, approx: op ? op.approx : true };
    if (!en && (ep || t.end_location || t.origin) && ld.inbound) en = { kind: "home", role: "도착", name: (ep && ep.name) || t.end_location || t.origin, label: (ep && ep.label) || t.end_location || t.origin, time: ld.inbound.arrive, leg: ld.inbound, approx: ep ? ep.approx : true };
    if (st && st.lat == null && op) st = Object.assign({}, st, { lat: op.lat, lng: op.lng });
    if (en && en.lat == null && ep) en = Object.assign({}, en, { lat: ep.lat, lng: ep.lng });
    return { st, en };
  }
  function homeLegLine(leg, plan, back) {
    if (!leg) return "";
    const g = plan.transport_guide;
    const dl = isDrive(g) ? (g.legs || []).find((l) => l.type === "drive") : null;
    const dist = dl && dl.distance_km != null ? ` · ${esc(Math.round(dl.distance_km))}km` : "";
    const hasRoute = isDrive(g) && Array.isArray(g.route_path) && g.route_path.length > 1;
    const via = back ? ((plan.return_stopovers && plan.return_stopovers.items) || []).filter((x) => x.added).map((x) => x.name) : [];
    return `<li class="leg long${back ? " back" : ""}"><span class="leg-in">${via.length ? `<span class="leg-via">${esc(((homePoints(plan).en || {}).name) || "집")}으로 가는 길: ${via.map(esc).join(", ")} 들렀다가</span>` : ""}${ico(isDrive(g) || leg.mode === "자차" ? "ico-car" : "ico-pin")}<b>${esc(leg.label || "")}</b> · ${esc(leg.mode || "")} 약 ${fmtMin(leg.duration_min)}${dist}${leg.estimated !== false ? " (추정)" : ""}${leg.depart && leg.arrive ? ` <span class="muted">${esc(leg.depart)}→${esc(leg.arrive)}</span>` : ""}
      ${(hasRoute || (homePoints(plan).st || {}).lat != null) ? `<button type="button" class="linkish" data-act="route-fit">${back ? "오는 길 보기" : "가는 길 보기"}</button>` : ""}</span></li>`;
  }
  /* 머무는 시간 · 개인 시간 */
  const isFree = (s) => !!s && (s.free || s.source === "개인 시간" || String(s.place_id || "").indexOf("FREE:") === 0 || (Array.isArray(s.notes) && s.notes.indexOf("개인 시간") >= 0));
  const hm2min = (t) => { const m = /(\d{1,2}):(\d{2})/.exec(String(t || "")); return m ? Number(m[1]) * 60 + Number(m[2]) : null; };
  const min2hm = (n) => `${pad2(Math.floor(((n % 1440) + 1440) % 1440 / 60))}:${pad2(((n % 60) + 60) % 60)}`;
  const stayMin = (s) => (s.stay_min != null ? Number(s.stay_min) : (hm2min(s.depart) != null && hm2min(s.arrive) != null ? hm2min(s.depart) - hm2min(s.arrive) : null));
  function stayBtn(s, name) {
    if (!canEdit() || s.place_id == null || s.type === "stay") return "";
    const m = stayMin(s);
    return `<button type="button" class="stay-ed" data-act="stay-open" data-pid="${esc(s.place_id)}" data-min="${esc(m == null ? 60 : m)}" data-arrive="${esc(s.arrive || "")}" data-name="${esc(name)}" aria-haspopup="dialog" title="머무는 시간 바꾸기">머무는 시간 ${m != null ? fmtMin(m) : "?"} ✎</button>`;
  }
  function freeCard(s, d, id, time) {
    const label = s.name || "개인 시간";
    const icon = /카페|커피/.test(label) ? "☕" : /쇼핑/.test(label) ? "🛍" : /산책/.test(label) ? "🚶" : "🧘";
    const grip = canEdit() ? `<button type="button" class="tl-grip" aria-label="${esc(label)} 옮기기 — 스페이스로 들고 ↑↓로 옮긴 뒤 스페이스로 놓아요" title="끌어서 순서 바꾸기">${ico("ico-grip")}</button>` : "";
    const ctl = canEdit() ? `<span class="free-ctl">${stayBtn(s, label)}<button type="button" class="btn xs danger" data-act="free-remove" data-pid="${esc(s.place_id)}" data-name="${esc(label)}">빼기</button></span>` : "";
    return `<li class="stop slim free${grip ? " has-grip" : ""}" id="${id}" data-day="${esc(d.day)}" data-pid="${esc(s.place_id)}">
      <span class="num" aria-hidden="true">${icon}</span>${grip}
      <div class="card slim-card free-card"><span class="time">${time}</span><span class="tag">개인 시간</span><b>${esc(label)}</b>${ctl}</div></li>`;
  }
  /* 작은 팝오버(머무는 시간 · 개인 시간) */
  const POP = { el: null, trigger: null, kind: null, data: null };
  function popOpen(trigger, kind, data) {
    popClose(false);
    let el = $("#mini-pop");
    if (!el) { el = document.createElement("div"); el.id = "mini-pop"; el.className = "tp-pop mini-pop"; el.setAttribute("role", "dialog"); document.body.appendChild(el); el.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); popClose(true); } }); el.addEventListener("input", popInput); }
    Object.assign(POP, { el, trigger, kind, data });
    el.hidden = false; trigger.setAttribute("aria-expanded", "true");
    popRender(true);
    const r = trigger.getBoundingClientRect(), w = Math.min(320, window.innerWidth - 16);
    el.style.width = w + "px";
    if (window.innerWidth < 640) { el.classList.add("sheet"); el.style.left = el.style.top = ""; }
    else { el.classList.remove("sheet"); const h = el.offsetHeight; let top = r.bottom + 6; if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6); el.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px"; el.style.top = top + "px"; }
  }
  function popClose(refocus) {
    if (!POP.el || POP.el.hidden) return;
    POP.el.hidden = true;
    if (POP.trigger) { POP.trigger.setAttribute("aria-expanded", "false"); if (refocus && POP.trigger.isConnected) POP.trigger.focus(); }
    POP.trigger = null;
  }
  function popRender(focus) {
    const el = POP.el, D = POP.data;
    if (POP.kind === "stay") {
      const a = hm2min(D.arrive), end = a != null ? a + D.min : null, delta = D.min - D.orig;
      el.setAttribute("aria-label", `${D.name} 머무는 시간`);
      el.innerHTML = `<div class="tp-head"><p class="tp-title">머무는 시간 · ${esc(D.name)}</p><button type="button" class="x" data-act="pop-close" aria-label="닫기">×</button></div>
        <div class="tp-g mp-chips">${[["-30", "−30분"], ["-15", "−15분"], ["+15", "+15분"], ["+30", "+30분"], ["=60", "1시간"], ["=120", "2시간"], ["=180", "3시간"]].map(([v, l]) => `<button type="button" class="tp-b" data-act="stay-chip" data-v="${v}">${l}</button>`).join("")}</div>
        <label class="st-in"><span>분(15~360)</span><input id="mp-min" type="number" min="15" max="360" step="5" value="${D.min}"></label>
        <p class="mp-prev" aria-live="polite">${fmtMin(D.min)}${end != null ? ` → <b>${min2hm(end)}</b> 종료` : ""}${delta ? `, 뒤 일정 ${Math.abs(delta)}분 ${delta > 0 ? "밀림" : "당겨짐"}` : ""}</p>
        <div class="tp-foot"><button type="button" class="btn sm ghost" data-act="pop-close">취소</button><button type="button" class="btn sm primary" data-act="stay-apply"${D.min === D.orig || D.min < 15 || D.min > 360 ? " disabled" : ""}>적용</button></div>`;
    } else {
      el.setAttribute("aria-label", "개인 시간 넣기");
      el.innerHTML = `<div class="tp-head"><p class="tp-title">개인 시간 넣기${D.afterName ? ` · ‘${esc(D.afterName)}’ 다음` : ` · ${esc(D.day)}일차`}</p><button type="button" class="x" data-act="pop-close" aria-label="닫기">×</button></div>
        <div class="tp-g mp-chips">${["휴식", "카페에서 쉬기", "자유 시간", "산책", "쇼핑"].map((l) => `<button type="button" class="tp-b${D.label === l ? " on" : ""}" data-act="free-label" data-v="${l}" aria-pressed="${D.label === l}">${l}</button>`).join("")}</div>
        <label class="st-in"><span>직접 적기(30자)</span><input id="mp-label" type="text" maxlength="30" value="${esc(D.label)}" autocomplete="off"></label>
        <div class="tp-g mp-chips">${[30, 45, 60, 90, 120].map((m) => `<button type="button" class="tp-b${D.min === m ? " on" : ""}" data-act="free-min" data-v="${m}" aria-pressed="${D.min === m}">${fmtMin(m)}</button>`).join("")}</div>
        <div class="tp-foot"><button type="button" class="btn sm ghost" data-act="pop-close">취소</button><button type="button" class="btn sm primary" data-act="free-apply"${D.label.trim() ? "" : " disabled"}>${fmtMin(D.min)} 넣기</button></div>`;
    }
    if (focus) { const f = el.querySelector(".tp-b, input"); if (f) f.focus(); }
  }
  function popInput(e) {
    const D = POP.data;
    if (e.target.id === "mp-min") { D.min = Math.round(Number(e.target.value) || 0); const pv = $(".mp-prev", POP.el); const a = hm2min(D.arrive); const delta = D.min - D.orig;
      if (pv) pv.innerHTML = `${fmtMin(D.min)}${a != null ? ` → <b>${min2hm(a + D.min)}</b> 종료` : ""}${delta ? `, 뒤 일정 ${Math.abs(delta)}분 ${delta > 0 ? "밀림" : "당겨짐"}` : ""}`;
      const ap = $('[data-act="stay-apply"]', POP.el); if (ap) ap.disabled = D.min === D.orig || D.min < 15 || D.min > 360; }
    if (e.target.id === "mp-label") { D.label = e.target.value; const ap = $('[data-act="free-apply"]', POP.el); if (ap) ap.disabled = !D.label.trim(); }
  }

  /* 아침 식사 · 숙소 안내 · 돌아오는 길 들를 곳 */
  const isBreakfast = (s) => s && s.type === "meal" && (/조식|아침/.test(s.name || "") || /조식|아침/.test(String(s.notes || "")));
  function prevStay(plan, d) {
    const its = plan.itinerary || [];
    const i = its.indexOf(d);
    const prev = i > 0 ? its[i - 1] : null;
    return prev ? (prev.stops || []).filter((x) => x.type === "stay").pop() || null : null;
  }
  function breakfastCard(s, d, plan, id, time) {
    const ml = plan.meals || {};
    const hotel = /숙소 조식/.test(s.name || "") || /숙소 조식/.test(String(s.notes || "")) || ml.breakfast === "숙소 조식";
    const stay = prevStay(plan, d);
    const hint = stay && stay.breakfast_hint && ml.breakfast !== "숙소 조식"
      ? `<p class="bf-hint">이 숙소는 조식 정보가 있어요 — 숙소 조식으로 바꾸면 이동 없이 30분 여유</p>` : "";
    const ctl = canEdit() ? `<span class="bf-ctl">
        <button type="button" class="btn xs ghost" data-act="bf-mode" data-v="${hotel ? "근처 식당" : "숙소 조식"}">${hotel ? "근처 식당으로" : "숙소 조식으로 바꾸기"}</button>
        <button type="button" class="btn xs danger" data-act="bf-skip" data-day="${esc(d.day)}">빼기</button></span>` : "";
    return `<li class="stop slim breakfast t-meal" id="${id}" data-day="${esc(d.day)}" data-pid="">
      <span class="num" aria-hidden="true">${hotel ? "🍳" : "☕"}</span>
      <div class="card slim-card bf-card"><span class="time">${time}</span><span class="tag">아침</span><b>${esc(s.name)}</b>${ctl}${hint}</div></li>`;
  }
  function breakfastSkipped(d, plan) {
    const ml = plan.meals || {};
    if (!canEdit() || !(ml.breakfast_skip_days || []).map(Number).includes(Number(d.day))) return "";
    return `<li class="stop slim breakfast skipped" data-pid=""><span class="num" aria-hidden="true">☕</span>
      <div class="card slim-card bf-card"><span class="muted">이 날 아침 식사를 뺐어요</span><button type="button" class="btn xs ghost" data-act="bf-restore" data-day="${esc(d.day)}">다시 넣기</button></div></li>`;
  }
  function lodgingNotice(d) {
    const n = d.lodging_notice;
    if (!n) return "";
    const k = n.kind === "연박" ? "stay" : n.kind === "이동" ? "move" : "last";
    const head = n.kind === "연박" ? "🧳 짐은 두고 가볍게" : n.kind === "이동" ? `🧳 짐 챙겨 체크아웃${n.checkout ? ` ${esc(n.checkout)}까지` : ""}` : `✅ 두고 온 물건 확인${n.checkout ? ` · 체크아웃 ${esc(n.checkout)}` : ""}`;
    return `<div class="lodge-notice k-${k}"><p class="ln-h"><b>${head}</b>${n.title ? `<span>${esc(n.title)}</span>` : ""}</p>${n.message ? `<p class="ln-m">${esc(n.message)}</p>` : ""}
      ${k === "last" ? `<ul class="ln-check" aria-label="챙길 것"><li>충전기·보조배터리</li><li>세면도구·화장품</li><li>옷걸이·욕실 속 빨래</li><li>냉장고 속 음식</li></ul>` : ""}</div>`;
  }
  function stopoversCard(plan) {
    const R = plan.return_stopovers;
    if (!R || !(R.items || []).length) return "";
    const added = (R.items || []).filter((x) => x.added).length || (R.added || []).length;
    const full = added >= 3;
    const items = (R.items || []).map((x) => {
      const img = x.image && /^https?:\/\//i.test(x.image) ? `<img src="${esc(x.image.replace(/^http:/, "https:"))}" alt="" loading="lazy">` : bird("bird");
      const btn = canEdit() ? (x.added
        ? `<button type="button" class="btn xs ghost" data-act="ret-remove" data-key="${esc(x.key || x.name)}" data-name="${esc(x.name)}">들를 곳에서 빼기</button>`
        : `<button type="button" class="btn xs primary" data-act="ret-add" data-key="${esc(x.key || x.name)}"${full ? ' disabled title="최대 3곳까지예요"' : ""}>+ 들르기</button>`) : "";
      return `<li class="so-item${x.added ? " added" : ""}"><div class="so-img">${img}</div>
        <div class="so-b"><p class="so-name"><b>${esc(x.name)}</b><span class="badge ${x.kind === "카페" ? "user" : "route"}">${esc(x.kind || "")}</span>${x.added ? `<span class="badge ok">들를 곳</span>` : ""}</p>
        <p class="so-meta">돌아가는 길 ${esc(x.km_from_start)}km 지점 · 우회 약 ${fmtMin(x.detour_min)} · 머무는 ${fmtMin(x.stay_min)}</p>${btn}</div></li>`;
    }).join("");
    return `<li class="stopovers" aria-label="돌아오는 길에 들를 곳"><div class="so-card"><p class="so-h"><b>아쉬우면 들러 가요</b><span class="muted">${esc(R.note || "")}${canEdit() ? ` · 최대 3곳` : ""}</span></p>
      <ul class="so-list">${items}</ul></div></li>`;
  }
  function homeCard(pt, end) {
    if (!pt) return "";
    const key = end ? "home-e" : "home-s";
    return `<li class="stop home ${end ? "home-end" : "home-start"}" id="stop-${key}" data-day="" data-pid="">
      <span class="num home-num" aria-hidden="true">${ico("ico-home")}</span>
      <div class="card home-card"><span class="home-role">${end ? "도착" : "출발"}</span>
        ${end ? `<b>${esc(pt.name || "")}</b>${pt.time ? `<span class="time">${esc(pt.time)} 도착</span>` : ""}`
              : `${pt.time ? `<span class="time">${esc(pt.time)}</span>` : ""}<b>${esc(pt.name || "")} 출발</b>`}
        ${pt.label && pt.label !== pt.name ? `<span class="muted">${esc(pt.label)}</span>` : ""}${pt.approx ? `<small class="home-approx">대략 위치</small>` : ""}</div></li>`;
  }

  function daySection(d, P, plan) {
    const color = dayColor(d.day);
    const stops = d.stops || [];
    const its = plan.itinerary || [];
    const hp = homePoints(plan);
    const isFirst = its[0] === d, isLast = its[its.length - 1] === d;
    const headHome = isFirst && hp.st ? homeCard(hp.st, false) + homeLegLine(hp.st.leg, plan, false) : "";
    const tailHome = isLast && hp.en ? stopoversCard(plan) + homeLegLine(hp.en.leg, plan, true) + homeCard(hp.en, true) : (isLast ? stopoversCard(plan) : "");
    const items = stops.map((s, i) => {
      const leg = findLeg(d, s);
      const legHtml = leg ? `<li class="leg" aria-label="이동 ${esc(km(leg.distance_km))}, 약 ${fmtMin(leg.duration_min)}">
          <span class="leg-in">${i === 0 && leg.from_name ? `<span class="leg-from">${esc(leg.from_name)}에서</span>` : ""}▶ ${esc(km(leg.distance_km))} · 약 ${fmtMin(leg.duration_min)} · ${esc(leg.mode || "")}${leg.estimated !== false ? " (추정)" : ""}</span></li>` : "";
      const ins = canEdit() && s.place_id != null && s.type !== "stay"
        ? `<li class="free-ins"><button type="button" class="free-add" data-act="free-open" data-after="${esc(s.place_id)}" data-day="${esc(d.day)}" data-name="${esc(s.name)}" aria-haspopup="dialog">${ico("ico-plus")}개인 시간</button></li>` : "";
      return legHtml + stopCard(s, i, stops.length, d, P, plan) + ins;
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
        ${canEdit() ? `<button type="button" class="btn sm re-open-btn noprint" data-act="re-open" data-day="${esc(d.day)}" aria-haspopup="dialog">${ico("ico-route")}동선 편집</button>` : ""}
      </header>
      ${lodgingNotice(d)}
      <ol class="stops">${headHome}${breakfastSkipped(d, plan)}${items || `<li class="empty">이 날은 아직 비어 있어요.</li>`}${tailHome}</ol>
      ${canEdit() ? `<div class="day-foot noprint"><button type="button" class="btn sm ghost" data-act="free-open" data-day="${esc(d.day)}" aria-haspopup="dialog">${ico("ico-plus")}개인 시간</button></div>` : ""}</section>`;
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
    if (!p && s.place_id == null && isBreakfast(s)) return breakfastCard(s, d, plan, id, time);
    if (isFree(s)) return freeCard(s, d, id, time);
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
    const okUrl = (u) => /^https?:\/\//i.test(String(u || ""));
    const homepage = okUrl(s.homepage) ? s.homepage : okUrl(place.homepage) ? place.homepage : null;
    let links = (place.links || []).filter((l) => l && okUrl(l.url)).slice(0, 4);
    if (!links.length && homepage) links = [{ label: "공식 홈페이지", url: homepage }];
    const feeCheck = s.fee_check != null ? !!s.fee_check : !!place.fee_check;
    const feeBadge = feeCheck ? (homepage
      ? `<a class="badge warn fee-chk" href="${esc(homepage)}" target="_blank" rel="noopener noreferrer">입장료 확인 · 홈페이지에서 입장료 확인 ↗</a>`
      : `<span class="badge warn fee-chk">입장료 확인 · 방문 전 입장료 확인 필요</span>`) : "";
    const feeText = place.fee ? `<span class="fee-t">요금 ${esc(place.fee)}</span>` : "";
    const linkRow = links.length ? `<p class="place-links">${links.map((l) => `<a class="link-btn" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer"><span aria-hidden="true">🔗</span> ${esc(l.label || "홈페이지")} ↗</a>`).join("")}</p>` : "";
    const mine = place.added_by_user ? `<span class="badge user">직접 추가</span>` : "";
    const reason = s.reason || place.reason;
    const comp = (plan.trip && plan.trip.companions) || {};
    const compLabel = comp.type === "노약자" ? "노약자 동반 참고" : /어린이|유아/.test(comp.type || "") ? "아이 동반 참고" : "동반자 참고";
    const info = [["휴무", place.closed], ["요금", place.fee], ["주차", place.parking], ["문의", place.contact]].filter((x) => x[1]);
    const nearRows = (arr, food) => (arr || []).slice(0, food ? 4 : 3).map((x) => {
      const inPlan = x.place_id != null && IN_PLAN.has(String(x.place_id));
      const act = inPlan ? `<span class="badge ok near-in">일정에 있음</span>`
        : canEdit() && x.place_id != null && s.place_id != null
          ? `<button type="button" class="near-add" data-act="near-add" data-pid="${esc(x.place_id)}" data-name="${esc(x.name)}" data-after="${esc(s.place_id)}" data-after-name="${esc(name)}" data-day="${esc(d.day)}" aria-label="${esc(name)} 다음에 ${esc(x.name)} 들르기">${ico("ico-plus")}이 다음에 들르기</button>` : "";
      return `<li class="near-row${inPlan ? " in" : ""}"><span class="near-n">${esc(x.name)}</span>${x.distance_km != null ? `<span class="near-d">${esc(x.distance_km)}km</span>` : ""}${x.note ? `<span class="near-note">${esc(x.note)}</span>` : ""}${act}</li>`;
    }).join("");
    const foodsBox = s.type === "place" && (place.foods || []).length ? `<div class="near-box"><h5>${ico("ico-fork")}근처 먹거리 <span class="muted">${canEdit() ? "눌러서 이 장소 바로 다음에 넣을 수 있어요" : ""}</span></h5><ul class="near-list">${nearRows(place.foods, true)}</ul></div>` : "";
    const details = [
      (place.highlights || []).length ? `<div class="d-block"><h5>이곳만의 볼거리</h5><ul class="hl">${place.highlights.map((h) => `<li>${esc(h)}</li>`).join("")}</ul></div>` : "",
      place.guide_tips ? `<div class="d-block"><h5>관람 팁</h5><p>${esc(place.guide_tips)}</p></div>` : "",
      place.companion_notes ? `<div class="d-block comp"><h5>${esc(compLabel)}</h5><p>${esc(place.companion_notes)}</p></div>` : "",
      (place.nearby_sights || []).length ? `<div class="d-block"><h5>근처 볼거리</h5><ul class="near-list">${nearRows(place.nearby_sights)}</ul></div>` : "",
      s.type !== "place" && (place.foods || []).length ? `<div class="d-block"><h5>근처 먹거리</h5><ul class="near-list">${nearRows(place.foods, true)}</ul></div>` : "",
      info.length ? `<dl class="info">${info.map((x) => `<div><dt>${x[0]}</dt><dd>${esc(x[1])}</dd></div>`).join("")}</dl>` : "",
      place.address ? `<p class="addr">${esc(place.address)}</p>` : "",
    ].join("");
    const hasDetails = !!details.trim();
    const swapKind = s.swap_kind || (s.type === "meal" ? (/카페|cafe/i.test(place.category || "") ? "카페" : "식당") : s.type === "stay" ? "숙소" : "관광지");
    const swapHint = s.swap_hint != null ? !!s.swap_hint : (s.type === "meal" || s.type === "stay" || !!place.added_by_user);
    const swapBtn = canEdit() && s.place_id != null
      ? (swapHint
        ? `<button type="button" class="btn sm swap-btn" data-act="swap" data-pid="${esc(s.place_id)}" data-kind="${esc(swapKind)}" data-name="${esc(name)}" aria-haspopup="dialog">${ico("ico-swap")}다른 ${esc(swapKind === "관광지" ? "곳" : swapKind)}으로 바꾸기</button>`
        : `<button type="button" class="btn xs ghost swap-mini" data-act="swap" data-pid="${esc(s.place_id)}" data-kind="${esc(swapKind)}" data-name="${esc(name)}" aria-haspopup="dialog" aria-label="${esc(name)} 다른 곳으로 바꾸기" title="다른 곳으로 바꾸기">${ico("ico-swap")}바꾸기</button>`)
      : "";
    const edit = canEdit() && s.place_id != null ? `<div class="edit" role="group" aria-label="${esc(name)} 편집">${swapHint ? "" : swapBtn}
        ${s.type !== "stay" && (plan.itinerary || []).length > 1 ? `<span class="move-days" role="group" aria-label="${esc(name)} 다른 날로 옮기기">${(plan.itinerary || []).filter((x) => x.day !== d.day).length <= 3
          ? (plan.itinerary || []).filter((x) => x.day !== d.day).map((x) => `<button type="button" class="btn xs move-day" data-act="tl-moveday" data-pid="${esc(s.place_id)}" data-day="${esc(x.day)}" style="--c:${dayColor(x.day)}">${ico("ico-swap")}${esc(x.day)}일차로 옮기기</button>`).join("")
          : `<label class="sr" for="md-${id}">${esc(name)} 옮길 날</label><select id="md-${id}" class="sel xs move-day-sel" data-pid="${esc(s.place_id)}"><option value="">다른 날로 옮기기…</option>${(plan.itinerary || []).filter((x) => x.day !== d.day).map((x) => `<option value="${esc(x.day)}">${esc(x.day)}일차로 옮기기</option>`).join("")}</select>`}</span>` : ""}
        <button type="button" class="btn xs ghost" data-act="re-open" data-day="${esc(d.day)}" data-focus="${esc(s.place_id)}">${ico("ico-route")}동선 편집</button>
        <button type="button" class="btn xs danger" data-act="remove" data-pid="${esc(s.place_id)}" data-day="${esc(d.day)}" data-name="${esc(name)}">빼기</button></div>` : "";
    const grip = canEdit() && s.place_id != null && s.type !== "stay"
      ? `<button type="button" class="tl-grip" aria-label="${esc(name)} 옮기기 — 스페이스로 들고 ↑↓로 옮긴 뒤 스페이스로 놓아요" title="끌어서 순서 바꾸기">${ico("ico-grip")}</button>` : "";
    return `<li class="stop t-${esc(s.type)}${grip ? " has-grip" : ""}${s.diff ? " is-diff" : ""}" id="${id}" data-day="${esc(d.day)}" data-pid="${esc(s.place_id)}">${num}${grip}
      <article class="card" aria-labelledby="${id}-h">
        ${mediaHtml(p, name)}
        <div class="body">
          <p class="meta"><span class="time">${time}</span>${stayBtn(s, name)}<span class="tag">${esc(cat)}</span>${s.diff === "new" ? `<span class="badge dnew">NEW</span>` : ""}${s.diff === "moved" ? `<span class="badge dmv">${esc(s.from_day)}→${esc(d.day)}일차</span>` : ""}${rank}${mine}${hoursBadge}${feeText}${feeBadge}</p>
          <h4 id="${id}-h">${esc(name)}</h4>
          ${reason ? `<p class="reason">${esc(reason)}</p>` : ""}
          ${place.summary ? `<p class="summary">${esc(place.summary)}</p>` : ""}
          ${foodsBox}
          ${hasDetails ? `<div class="details" id="${id}-d">${details}</div>` : ""}
          ${s.type === "stay" && (s.checkin || s.checkout || s.stay_note) ? `<p class="stay-info">${s.consecutive ? `<span class="badge ok">연박</span>` : ""}${Number(s.nights_here) > 1 ? `<span class="badge route">${esc(s.nights_here)}박</span>` : ""}
            ${s.consecutive ? "" : `<span>${s.checkin ? `체크인 <b>${esc(s.checkin)}</b>${s.checkin_known === false ? "(추정)" : ""}` : ""}${s.checkout ? ` · 체크아웃 다음 날 <b>${esc(s.checkout)}</b>${s.checkout_known === false ? "(추정)" : ""}` : ""}</span>`}
            ${s.stay_note ? `<span class="muted">${esc(s.stay_note)}</span>` : ""}</p>` : ""}
          ${linkRow}
          <div class="card-foot">
            ${hasDetails ? `<button type="button" class="more" data-act="more" aria-expanded="false" aria-controls="${id}-d">자세히${ico("ico-chev", "ico chev")}</button>` : ""}
            <span class="src">출처: ${esc(place.source || s.source || "한국관광공사 TourAPI")}</span>
          </div>
          ${swapHint ? swapBtn : ""}
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
    // ITS 기반 도로 정보
    const srcCls = (x) => /실시간/.test(x || "") ? "live" : /ITS|예측/.test(x || "") ? "pred" : "avg";
    const srcB = (x) => x ? `<span class="tg-src-b ${srcCls(x)}">${esc(x)}</span>` : "";
    const more = (n, key) => n > 3 ? `<button type="button" class="linkish tg-morebtn" data-act="tg-more" aria-expanded="false" data-k="${key}">더 보기 (${n - 3})</button>` : "";
    const segs = g.road_segments || [];
    const segHtml = segs.length ? `<div class="tg-block" data-more="seg"><p class="tg-sub">도로별 예상 ${(g.traffic_sources || []).map(srcB).join("")}</p>
      <div class="mm-scroll tg-scroll"><table class="tbl tg-segs"><thead><tr><th scope="col">도로</th><th scope="col">거리</th><th scope="col">막힘 없을 때 → 예상</th><th scope="col">속도</th></tr></thead><tbody>
      ${segs.map((r, i) => `<tr class="${i >= 3 ? "x-more" : ""}"><th scope="row">${esc(r.road || "")}${r.ref ? ` <small>${esc(r.ref)}</small>` : ""}</th><td class="num-c">${r.km != null ? esc(Math.round(r.km)) + "km" : "-"}</td>
        <td class="num-c">${r.free_min != null ? fmtMin(r.free_min) : "-"} → <b>${r.min != null ? fmtMin(r.min) : "-"}</b></td><td class="num-c">${r.speed_kmh != null ? esc(Math.round(r.speed_kmh)) + "km/h" : "-"} ${srcB(r.source)}</td></tr>`).join("")}</tbody></table></div>${more(segs.length, "seg")}</div>` : "";
    const evs = g.road_events || [];
    const evHtml = evs.length ? `<div class="tg-block" data-more="ev"><p class="tg-sub">${ico("ico-alert")}돌발 상황 ${evs.length}건</p><ul class="tg-evs">
      ${evs.map((e, i) => `<li class="tg-ev t-${/사고/.test(e.type || "") ? "acc" : /공사|작업/.test(e.type || "") ? "work" : /기상/.test(e.type || "") ? "wx" : "etc"}${i >= 3 ? " x-more" : ""}">
        <span class="tg-ev-ic">${ico("ico-alert")}</span><b>${esc(e.type || "알림")}</b>${e.road ? `<span class="tg-ev-road">${esc(e.road)}</span>` : ""}<span class="tg-ev-msg">${esc(e.message || e.detail || "")}</span>
        ${e.lanes_blocked ? `<span class="badge warn">차로 차단${typeof e.lanes_blocked === "number" ? ` ${esc(e.lanes_blocked)}` : ""}</span>` : ""}${Number(e.delay_min) > 0 ? `<span class="badge warn">+${esc(e.delay_min)}분</span>` : ""}</li>`).join("")}</ul>${more(evs.length, "ev")}</div>` : "";
    const cts = g.road_cautions || [];
    const ctHtml = cts.length ? `<div class="tg-block" data-more="ct"><p class="tg-sub">주의운전 구간 ${cts.length}곳</p><ul class="tg-cts">
      ${cts.map((x, i) => `<li class="${i >= 3 ? "x-more" : ""}" title="${esc(x.message || "")}"><b>${esc(x.type || "주의")}</b>${x.road ? ` · ${esc(x.road)}` : ""}${x.message ? `<span>${esc(x.message)}</span>` : ""}</li>`).join("")}</ul>${more(cts.length, "ct")}</div>` : "";
    const rd = g.return_drive;
    const retHtml = rd && rd.total_min != null ? `<p class="tg-ret">${ico("ico-home")}돌아올 때 약 <b>${fmtMin(rd.total_min)}</b>${rd.free_min != null ? ` <span class="muted">(막힘 없을 때 ${fmtMin(rd.free_min)})</span>` : ""}${(rd.traffic_sources || []).map(srcB).join("")}${rd.reason ? ` <span class="muted">· ${esc(rd.reason)}</span>` : ""}</p>` : "";
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
      ${retHtml}${segHtml}${evHtml}${ctHtml}
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

  /* ───────── 다른 곳으로 바꾸기 (대안 top 20) ───────── */
  const SW = { pid: null, name: "", kind: "", q: "", sort: "score", openOnly: false, data: null, loading: false, error: "", expanded: {}, seq: 0, last: null };
  function swapDialog() {
    let d = $("#swap-dlg");
    if (!d) {
      d = document.createElement("dialog");
      d.id = "swap-dlg"; d.className = "swap-dlg"; d.setAttribute("aria-labelledby", "swap-h");
      document.body.appendChild(d);
      d.addEventListener("close", () => { if (SW.last && SW.last.isConnected) SW.last.focus(); });
      d.addEventListener("click", (e) => { if (e.target === d) closeSwap(); }); // 바깥(backdrop) 클릭
      d.addEventListener("submit", (e) => {
        const f = e.target.closest("form[data-form='swap-q']");
        if (!f) return;
        e.preventDefault(); e.stopPropagation();
        SW.q = String(f.querySelector("input").value || "").trim();
        fetchSwap();
      });
    }
    return d;
  }
  function openSwap(pid, name, kind) {
    if (!canEdit()) return;
    Object.assign(SW, { pid, name: name || "", kind: kind || "", q: "", sort: "score", openOnly: false, data: null, error: "", expanded: {}, last: document.activeElement });
    const d = swapDialog();
    renderSwap();
    if (d.showModal) d.showModal(); else d.setAttribute("open", "");
    const inp = $("#swap-q"); if (inp) inp.focus();
    fetchSwap();
  }
  function closeSwap() { const d = $("#swap-dlg"); if (d) { if (d.close) d.close(); else d.removeAttribute("open"); } }
  async function fetchSwap() {
    const seq = ++SW.seq;
    SW.loading = true; SW.error = ""; renderSwap();
    let j = null;
    try {
      if (DEMO) {
        const r = await fetch("/static/sample_alternatives.json"); j = r.ok ? await r.json() : null;
        if (j && SW.q) j = Object.assign({}, j, { items: (j.items || []).filter((x) => (x.name + " " + (x.category || "")).indexOf(SW.q) >= 0) });
        if (j) j = Object.assign({}, j, { target: Object.assign({}, j.target, { place_id: SW.pid, name: SW.name, kind_label: SW.kind || j.target.kind_label }) });
      } else {
        const u = `/places/alternatives?session_id=${encodeURIComponent(state.sessionId || "")}&place_id=${encodeURIComponent(SW.pid)}&limit=20${SW.q ? `&q=${encodeURIComponent(SW.q)}` : ""}`;
        const r = await fetch(u); j = await r.json().catch(() => null);
        if (!r.ok) throw new Error((j && (j.answer || j.message)) || "대안을 불러오지 못했어요");
      }
    } catch (e) { if (seq === SW.seq) { SW.error = e.message || "대안을 불러오지 못했어요. 잠시 후 다시 해 주세요."; } }
    if (seq !== SW.seq) return;
    SW.loading = false; SW.data = j; renderSwap();
  }
  function swapItems() {
    const D = SW.data || {};
    let items = (D.items || []).map((x, i) => Object.assign({ _i: i }, x));
    if (SW.openOnly) items = items.filter((x) => x.hours_status === "확인됨");
    const num = (v, d) => (v == null || isNaN(v) ? d : Number(v));
    const by = {
      score: (a, b) => num(b.score, -1) - num(a.score, -1),
      near: (a, b) => num(a.distance_km, 1e9) - num(b.distance_km, 1e9),
      popular: (a, b) => num(a.popular_rank, 1e9) - num(b.popular_rank, 1e9),
      reviews: (a, b) => num(b.review_count, -1) - num(a.review_count, -1),
      rating: (a, b) => num(b.rating, -1) - num(a.rating, -1),
    }[SW.sort] || (() => 0);
    return items.sort(by).slice(0, 20);
  }
  function renderSwap() {
    const d = $("#swap-dlg");
    if (!d) return;
    const D = SW.data || {};
    const T = D.target || {};
    const kind = T.kind_label || SW.kind || "장소";
    const fa = document.activeElement && d.contains(document.activeElement) ? (document.activeElement.id || document.activeElement.dataset.k) : null;
    const crit = D.criteria || [];
    const hasPop = (D.items || []).some((x) => x.popular_rank != null);
    const sorts = [["score", "추천 점수"], ["near", "가까운 순"]].concat(hasPop ? [["popular", "인기 순"]] : [], D.has_reviews ? [["reviews", "리뷰 많은 순"], ["rating", "평점 순"]] : []);
    const items = swapItems();
    const when = [T.day ? `${T.day}일차` : "", T.arrive || ""].filter(Boolean).join(" ");
    let body;
    if (SW.loading && !SW.data) body = `<p class="swap-empty">모모가 근처 ${esc(kind)}을(를) 찾는 중이에요…</p>`;
    else if (SW.error) body = `<p class="swap-empty">${esc(SW.error)}</p>`;
    else if (!items.length) body = `<div class="swap-empty"><p>${SW.openOnly ? "그날 운영이 확인된 곳이 없어요. ‘그날 운영만’을 꺼 보세요." : "조건에 맞는 곳을 찾지 못했어요. 가게 이름을 적어서 다시 찾아 보세요."}</p>
        <button type="button" class="linkish" data-act="swap-search" data-k="go-search">장소 찾기 · 직접 추가로 가기</button></div>`;
    else body = `<ol class="swap-list">${items.map((x, n) => {
      const i = x._i;
      const sc = Math.max(0, Math.min(100, Number(x.score) || 0));
      const hs = x.hours_status === "휴무" ? ["bad", "그날 휴무"] : x.hours_status === "확인됨" ? ["ok", x.hours ? `운영 ${x.hours}` : "그날 운영"] : ["warn", "운영시간 확인 필요"];
      const open = !!SW.expanded[i];
      return `<li class="swap-item${x.hours_status === "휴무" ? " closed" : ""}">
        <span class="swap-rank" aria-label="${n + 1}위">${n + 1}</span>
        <div class="swap-img">${x.image && /^https:\/\//i.test(x.image) ? `<img src="${esc(x.image)}" alt="" loading="lazy" title="${esc(x.image_source ? "사진: " + x.image_source : "")}">` : bird("bird")}</div>
        <div class="swap-b">
          <p class="swap-name"><b>${esc(x.name)}</b>${x.category ? `<span class="tag">${esc(x.category)}</span>` : ""}<span class="badge ${/osm|openstreet/i.test(x.source || "") ? "draft" : "route"}">${esc(x.source || "")}</span></p>
          <p class="swap-meta">${x.distance_km != null ? `약 ${esc(Number(x.distance_km).toFixed(1))}km` : ""}<span class="badge ${hs[0]}">${esc(hs[1])}</span>${x.popular_rank != null ? `<span class="muted">인기 ${esc(x.popular_rank)}위</span>` : ""}${x.rating != null ? `<span class="muted">평점 ${esc(x.rating)}</span>` : ""}${x.review_count != null ? `<span class="muted">리뷰 ${esc(x.review_count)}</span>` : ""}</p>
          ${x.why ? `<p class="swap-why">${esc(x.why)}</p>` : ""}
          <div class="swap-score">
            <button type="button" class="swap-sc" data-act="swap-parts" data-i="${i}" data-k="parts-${i}" aria-expanded="${open}" aria-controls="sp-${i}" title="점수 구성 보기">
              <span class="swap-num"><b>${esc(sc)}</b>점</span><span class="swap-bar" aria-hidden="true"><span style="width:${sc}%"></span></span>${ico("ico-chev", "ico chev")}</button>
            ${open ? `<dl class="swap-parts" id="sp-${i}">${crit.map((c) => { const v = x.parts ? x.parts[c.key] : null; return `<div><dt>${esc(c.label)}</dt><dd><span class="swap-pbar" aria-hidden="true"><span style="width:${c.weight ? Math.round(((v || 0) / c.weight) * 100) : 0}%"></span></span>${v != null ? esc(v) : "-"}/${esc(c.weight)}</dd></div>`; }).join("")}</dl>` : ""}
          </div>
        </div>
        <button type="button" class="btn sm primary swap-go" data-act="swap-pick" data-i="${i}" data-k="pick-${i}"${state.busy ? " disabled" : ""}>이걸로 바꾸기</button></li>`;
    }).join("")}</ol>`;
    d.innerHTML = `<div class="swap-head">
        <h2 id="swap-h">${esc(kind)} 바꾸기 <span>· ${esc(when)}${when ? " " : ""}‘${esc(T.name || SW.name)}’ 대신</span></h2>
        <button type="button" class="x" data-act="swap-close" data-k="close" aria-label="닫기">×</button></div>
      <form class="swap-q" data-form="swap-q" role="search"><label class="sr" for="swap-q">이름이나 메뉴로 찾기</label>
        <input id="swap-q" type="search" value="${esc(SW.q)}" placeholder="이름·메뉴로 다시 찾기 (예: 칼국수)" autocomplete="off" maxlength="40"><button class="btn sm ghost">찾기</button></form>
      <div class="swap-tools" role="group" aria-label="정렬과 거르기">
        ${sorts.map(([v, t]) => `<button type="button" class="chip sm${SW.sort === v ? " on" : ""}" aria-pressed="${SW.sort === v}" data-act="swap-sort" data-v="${v}" data-k="sort-${v}">${t}</button>`).join("")}
        <span class="swap-sep" aria-hidden="true"></span>
        <button type="button" class="chip sm${SW.openOnly ? " on" : ""}" aria-pressed="${SW.openOnly}" data-act="swap-open" data-k="open">그날 운영만</button>
        ${SW.loading && SW.data ? `<span class="muted small">다시 찾는 중…</span>` : ""}
      </div>
      <div class="swap-body" aria-live="polite">${body}</div>
      ${D.note ? `<p class="swap-note">${esc(D.note)}</p>` : ""}
      ${(D.sources || []).length ? `<p class="swap-note">찾아본 곳: ${D.sources.map(esc).join(" · ")}</p>` : ""}`;
    if (fa) { const el = fa === "swap-q" ? $("#swap-q") : d.querySelector(`[data-k="${cssEsc(fa)}"]`); if (el) el.focus(); }
  }
  function chooseSwap(i) {
    const D = SW.data || {};
    const x = (D.items || [])[i];
    if (!x) return;
    const oldName = (D.target && D.target.name) || SW.name;
    closeSwap();
    send({ question: `${oldName} → ${x.name} 바꾸기`, action: "edit", edit: { op: "replace", target: SW.pid, choice: x.choice } }, "edit");
  }

  function sendTitle(title) {
    if (DEMO) { toast("데모 모드에서는 바뀌지 않아요"); renderBrochure({}); return; }
    send({ question: title ? `여행 제목을 ‘${title}’로` : "여행 제목을 기본으로", action: "edit", edit: { op: "title", title } }, "edit");
  }
  function renameTrip(btn) {
    const li = btn.closest(".trip-item"), h = btn.closest("h3");
    if (!li || !h) return;
    h.outerHTML = `<form class="title-ed" data-form="trip-title" data-sid="${esc(btn.dataset.sid)}"><label class="sr" for="tt-${esc(btn.dataset.sid)}">여행 이름</label>
      <input id="tt-${esc(btn.dataset.sid)}" type="text" maxlength="40" value="${esc(btn.dataset.title)}" autocomplete="off"><button class="btn xs primary">저장</button></form>`;
    const i = li.querySelector("input"); if (i) { i.focus(); i.select(); i.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); openTrips(); } }); }
  }
  async function saveTripTitle(sid, title) {
    if (DEMO) { toast("데모 모드에서는 바뀌지 않아요"); return; }
    if (sid === state.sessionId) { closeTrips(); sendTitle(title); return; }
    let dv = null;
    try { const r = await fetch("/trips/" + encodeURIComponent(sid)); const j = await r.json(); dv = j && j.draft_version; } catch (e) { dv = null; }
    const save = { sid: state.sessionId, dv: state.draftVersion };
    state.sessionId = sid; state.draftVersion = dv;
    const j = await callAsync({ question: `여행 제목을 ‘${title}’로`, action: "edit", edit: { op: "title", title } });
    state.sessionId = save.sid; state.draftVersion = save.dv;
    toast(j && j.status !== "오류" ? (j.changes && j.changes.length ? j.changes : ["여행 이름을 바꿨어요"]) : ((j && j.answer) || "이름을 바꾸지 못했어요"));
    openTrips();
  }

  /* ───────── 되돌리기 (POST /trips/{sid}/undo) ───────── */
  async function doUndo() {
    if (state.busy) { toast("모모가 아직 날아가는 중이에요. 잠시만 기다려 주세요."); return; }
    if (DEMO) { toast("데모 모드에서는 바뀌지 않아요"); return; }
    if (!state.sessionId) return;
    setBusy(true, "undo"); setMood("thinking");
    let r = null, j = null;
    try { r = await fetch(`/trips/${encodeURIComponent(state.sessionId)}/undo`, { method: "POST" }); j = await r.json().catch(() => null); } catch (e) { r = null; }
    setBusy(false, "undo");
    if (!r) { setMood("sorry"); toast("앗, 연결이 끊겼어요. 다시 해 주실래요?"); return; }
    if (r.status === 404) { setMood("idle"); state.undoCount = 0; renderBrochure({}); toast((j && j.error) || "되돌릴 수정이 없어요"); return; }
    if (r.status === 409) { setMood("idle"); toast("다른 작업을 처리하는 중이에요. 끝나면 다시 눌러 주세요."); return; }
    if (!r.ok || !j) { setMood("sorry"); toast((j && (j.error || j.answer)) || "되돌리지 못했어요"); return; }
    const st = j.status === "초안" ? "승인 대기" : j.status;
    handleResponse(Object.assign({}, j, { status: st, quick_replies: [], answer: "" }), "undo");
    toast(Array.isArray(j.changes) && j.changes.length ? j.changes : ["직전 수정을 되돌렸어요"], 5000);
  }

  /* ───────── 타임라인에서 바로 끌어서 순서 바꾸기 (수정 모드) ───────── */
  let TL = null;
  const tlServerOrder = () => (state.plan.itinerary || []).map((d) => ({ day: d.day, pids: (d.stops || []).filter((x) => x.place_id != null).map((x) => String(x.place_id)) }));
  function tlDomOrder() {
    return $$("#brochure .day").map((sec) => ({ day: Number(sec.dataset.day), pids: $$(".stops > .stop", sec).map((x) => x.dataset.pid).filter(Boolean) }));
  }
  function tlCandidates(list, except) { return $$(":scope > .stop", list).filter((x) => x !== except && x.dataset.pid && !x.classList.contains("home")); }
  function tlPlace(li, list, before) {
    // 숙소(.t-stay)는 그날 마지막, 출발·도착 집 카드는 맨 앞·맨 뒤에 고정
    const cands = tlCandidates(list, li);
    const stay = cands.find((x) => x.classList.contains("t-stay"));
    if (!before || (stay && cands.indexOf(before) > cands.indexOf(stay))) before = stay || $(":scope > .stop.home-end", list) || null;
    if (before) list.insertBefore(li, before);
    else list.appendChild(li);
  }
  function tlMoveToDay(pid, day) {
    if (!canEdit()) return;
    if (state.busy) { toast("앞의 수정을 적용하는 중이에요. 잠시만요."); return; }
    const li = $(`#brochure .stop[data-pid="${cssEsc(pid)}"]`);
    const sec = $(`#brochure .day[data-day="${day}"]`);
    if (!li || !sec) return;
    $("#brochure").classList.add("tl-dragging");
    tlPlace(li, $(".stops", sec), null);
    li.scrollIntoView({ block: "center", behavior: scrollBehavior() });
    li.classList.add("flash");
    tlCommit(pid);
  }
  function tlStart(li, kb) {
    if (state.busy) { toast("앞의 수정을 적용하는 중이에요. 잠시만요."); return false; }
    TL = { li, kb, ghost: null, dy: 0 };
    li.classList.add("tl-ph");
    $("#brochure").classList.add("tl-dragging");
    return true;
  }
  function tlEnd(commit) {
    if (!TL) return;
    const { li, ghost } = TL;
    if (TL.timer) clearInterval(TL.timer);
    if (ghost) ghost.remove();
    li.classList.remove("tl-ph");
    TL = null;
    document.removeEventListener("pointermove", tlMove);
    if (commit) tlCommit(li.dataset.pid); else { renderBrochure({}); }
  }
  function tlCommit(pid) {
    const srv = tlServerOrder(), now = tlDomOrder();
    const days = now.filter((d) => { const o = srv.find((x) => x.day === d.day); return !o || o.pids.join() !== d.pids.join(); }).map((d) => ({ day: d.day, place_ids: d.pids }));
    if (!days.length) { renderBrochure({}); return; }
    if (DEMO) { toast("데모 모드에서는 바뀌지 않아요"); renderBrochure({}); return; }
    days.forEach((d) => { const sec = $(`#brochure .day[data-day="${d.day}"]`); if (sec) sec.classList.add("tl-applying"); });
    state.focusAfter = pid;
    toast(`${days.map((d) => d.day + "일차").join("·")} 순서를 적용하는 중…`, 60000);
    send({ question: `[동선 편집] ${days.map((d) => d.day + "일차").join("·")} 순서 바꾸기`, action: "edit", edit: { op: "order", days } }, "edit");
  }
  function tlPointerDown(e) {
    const g = e.target.closest && e.target.closest(".tl-grip");
    if (!g || e.button > 0 || !canEdit()) return;
    const li = g.closest(".stop");
    e.preventDefault();
    if (!tlStart(li, false)) return;
    const r = li.getBoundingClientRect();
    const ghost = document.createElement("div");
    ghost.className = "tl-ghost";
    ghost.innerHTML = `${ico("ico-grip")}<b>${esc(($("h4", li) || {}).textContent || "")}</b>`;
    ghost.style.left = Math.max(8, e.clientX - 20) + "px"; ghost.style.top = (e.clientY - 20) + "px"; ghost.style.width = Math.min(r.width, 360) + "px";
    document.body.appendChild(ghost);
    TL.ghost = ghost;
    document.addEventListener("pointermove", tlMove);
    document.addEventListener("pointerup", () => tlEnd(true), { once: true });
    document.addEventListener("pointercancel", () => tlEnd(false), { once: true });
  }
  function tlMove(e) {
    if (!TL || !TL.ghost) return;
    TL.ghost.style.top = (e.clientY - 20) + "px"; TL.ghost.style.left = Math.max(8, e.clientX - 20) + "px";
    const sec = (document.elementsFromPoint ? document.elementsFromPoint(e.clientX, e.clientY) : [document.elementFromPoint(e.clientX, e.clientY)])
      .map((x) => x && x.closest && x.closest("#brochure .day")).find(Boolean);
    const list = sec && $(".stops", sec);
    if (list) {
      const cands = tlCandidates(list, TL.li);
      let before = null;
      for (const c of cands) { const rr = c.getBoundingClientRect(); if (e.clientY < rr.top + Math.min(rr.height / 2, 80)) { before = c; break; } }
      tlPlace(TL.li, list, before);
    }
    // 가장자리 자동 스크롤: 포인터가 가장자리에 머무는 동안 계속 굴린다
    const sc = MQ_PC.matches ? $("#scroller") : null;
    const bar = !sc ? $(".m-condbar") : null; // 모바일: 위에 붙은 조건 바 아래부터
    const top = sc ? sc.getBoundingClientRect().top : (bar ? Math.max(0, bar.getBoundingClientRect().bottom) : 0);
    const bot = sc ? sc.getBoundingClientRect().bottom : (($("#composer") && $("#composer").getBoundingClientRect().top) || window.innerHeight);
    TL.x = e.clientX; TL.y = e.clientY;
    TL.dy = e.clientY < top + 60 ? -1 : e.clientY > bot - 80 ? 1 : 0;
    if (TL.dy && !TL.timer) {
      TL.timer = setInterval(() => {
        if (!TL || !TL.dy) { if (TL) { clearInterval(TL.timer); TL.timer = 0; } return; }
        if (sc) sc.scrollTop += TL.dy * 14; else window.scrollBy(0, TL.dy * 14);
        tlMove({ clientX: TL.x, clientY: TL.y });
      }, 30);
    }
  }
  function tlKeydown(e) {
    const g = e.target.closest && e.target.closest(".tl-grip");
    if (!g) return;
    const li = g.closest(".stop");
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      if (TL && TL.li === li) { toast("놓았어요"); tlEnd(true); }
      else if (!TL && tlStart(li, true)) toast(["들었어요", "↑↓로 옮기고 스페이스로 놓아요 · Esc는 취소"], 6000);
      return;
    }
    if (!TL || TL.li !== li) return;
    if (e.key === "Escape") { e.preventDefault(); toast("옮기기를 취소했어요"); tlEnd(false); return; }
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    const list = li.parentNode;
    const cands = tlCandidates(list, li).filter((x) => !x.classList.contains("t-stay"));
    const all = $$(":scope > .stop", list).filter((x) => x.dataset.pid && !x.classList.contains("home") && !x.classList.contains("t-stay"));
    const i = all.indexOf(li);
    const secs = $$("#brochure .day");
    const si = secs.indexOf(list.closest(".day"));
    if (e.key === "ArrowUp") {
      if (i > 0) list.insertBefore(li, all[i - 1]);
      else if (si > 0) { const pl = $(".stops", secs[si - 1]); tlPlace(li, pl, null); }
    } else {
      if (i < all.length - 1) { const nx = all[i + 1]; list.insertBefore(li, nx.nextSibling); }
      else if (si < secs.length - 1) { const nl = $(".stops", secs[si + 1]); tlPlace(li, nl, tlCandidates(nl, li)[0] || null); }
    }
    void cands;
    g.focus();
    li.scrollIntoView({ block: "nearest" });
    const day = li.closest(".day");
    toast(`${day ? day.dataset.day : ""}일차 ${$$(":scope > .stop", li.parentNode).filter((x) => x.dataset.pid && !x.classList.contains("home")).indexOf(li) + 1}번째`, 2000);
  }

  /* ───────── 동선 편집 (끌어서 순서 바꾸기 · 최적화) ───────── */
  const RE = { scope: null, open: false, days: [], orig: [], dirty: {}, opt: null, optErr: "", focusDay: null, last: null, origPos: {} };
  function reSnapshot(plan) {
    const P = placeIndex(plan);
    return (plan.itinerary || []).map((d) => ({
      day: d.day, date: d.date, travel: d.day_travel_min, dist: d.day_distance_km,
      items: (d.stops || []).filter((s) => s.place_id != null).map((s) => {
        const leg = findLeg(d, s);
        const role = s.role || (s.type === "stay" ? "stay" : s.type === "meal" ? (String(s.arrive || "") < "15:00" ? "lunch" : "dinner") : "place");
        const c = coordOf(s, P);
        return { pid: String(s.place_id), name: s.name, type: s.type, role, arrive: s.arrive || "", inMin: leg ? leg.duration_min : null, lat: c ? c[0] : null, lng: c ? c[1] : null };
      }),
    }));
  }
  function openRouteEditor(day, focusPid) {
    if (!state.plan || !canEdit()) return;
    RE.days = reSnapshot(state.plan);
    RE.orig = JSON.parse(JSON.stringify(RE.days));
    RE.origPos = {};
    RE.orig.forEach((d) => d.items.forEach((it, i) => { RE.origPos[it.pid] = { day: d.day, i }; }));
    RE.dirty = {}; RE.open = true; RE.focusDay = day; RE.last = document.activeElement;
    if (!RE.scope) RE.scope = RE.days.length > 1 ? "all" : "day";
    let el = $("#re-panel");
    if (!el) {
      el = document.createElement("dialog");
      el.id = "re-panel"; el.className = "re-dlg"; el.setAttribute("aria-labelledby", "re-h");
      document.body.appendChild(el);
      el.addEventListener("pointerdown", reDragStart);
      el.addEventListener("keydown", reKeydown);
      el.addEventListener("cancel", (e) => { e.preventDefault(); closeRouteEditor(); });
    }
    el.innerHTML = `<div class="re-main" id="re-content"></div>
      <aside class="re-side" aria-label="미리보기 지도">
        <button type="button" class="re-maptoggle" data-act="re-maptoggle" aria-expanded="${MQ_PC.matches}" aria-controls="re-mapbox">${ico("ico-pin")}<span>미리보기 지도</span>${ico("ico-chev", "ico chev")}</button>
        <div id="re-mapbox" class="re-mapbox"${MQ_PC.matches ? "" : " hidden"}><div id="re-map" class="re-map"></div>
        <p class="legend">편집 중인 순서대로 그려요 · 점선은 직선 순서예요</p></div></aside>`;
    if (el.showModal) { if (!el.open) el.showModal(); } else el.setAttribute("open", "");
    document.body.classList.add("re-open");
    const tst = $("#toast"); if (tst) el.appendChild(tst); // 모달(최상위 레이어) 안에서도 알림이 보이게
    renderRouteEditor();
    initReMap();
    const target = focusPid ? el.querySelector(`.re-item[data-pid="${cssEsc(focusPid)}"] .re-grip`) : day ? el.querySelector(`.re-day[data-day="${day}"]`) : null;
    if (target) { target.scrollIntoView({ block: "center" }); if (target.focus) target.focus(); } else { const h = $("#re-h"); if (h) h.focus(); }
    loadOptimize();
  }
  function closeRouteEditor() {
    const el = $("#re-panel");
    RE.open = false;
    if (RE.map) { RE.map.remove(); RE.map = null; RE.layer = null; }
    const tst = $("#toast"); if (tst && el && el.contains(tst)) document.body.appendChild(tst);
    if (el) { if (el.close && el.open) el.close(); else el.removeAttribute("open"); el.innerHTML = ""; }
    document.body.classList.remove("re-open");
    setTimeout(refitMap, 30); // 배경 지도는 건드리지 않고 크기만 다시 맞춘다
    if (RE.last && RE.last.isConnected) RE.last.focus();
  }
  function reLoadFromPlan() {
    RE.days = reSnapshot(state.plan);
    RE.orig = JSON.parse(JSON.stringify(RE.days));
    RE.origPos = {};
    RE.orig.forEach((d) => d.items.forEach((it, i) => { RE.origPos[it.pid] = { day: d.day, i }; }));
    RE.dirty = {};
  }
  /* 편집기 전용 미니 지도 (열 때 만들고 닫을 때 없앤다) */
  function initReMap() {
    const box = $("#re-map");
    if (!box || !window.L || RE.map) return;
    const lay = MAPL.list.find((l) => l.id === MAPL.cur) || OSM_LAYER;
    RE.map = L.map(box, { zoomControl: true, attributionControl: true });
    L.tileLayer(lay.url, { attribution: lay.attribution || "", maxZoom: lay.max_zoom || 19, minZoom: lay.min_zoom || 0, subdomains: "abc" }).addTo(RE.map);
    if (lay.overlay_url) L.tileLayer(lay.overlay_url, { maxZoom: lay.max_zoom || 19, subdomains: "abc" }).addTo(RE.map);
    RE.layer = L.layerGroup().addTo(RE.map);
    RE.map.setView([35.5, 127.5], 8);
    setTimeout(() => { if (RE.map) { RE.map.invalidateSize(); drawReMap(true); } }, 80);
  }
  function drawReMap(fit) {
    if (!RE.map || !RE.layer) return;
    RE.layer.clearLayers();
    const byPid = {};
    RE.days.forEach((d) => d.items.forEach((it) => { byPid[it.pid] = it; }));
    const order = RE.dragOrder || RE.days.map((d) => ({ day: d.day, pids: d.items.map((x) => x.pid) }));
    const all = [];
    order.forEach((o) => {
      const color = dayColor(o.day);
      const hot = RE.hotDay && RE.hotDay === o.day;
      const pts = [];
      o.pids.forEach((pid, i) => {
        const it = byPid[pid];
        if (!it || it.lat == null) return;
        const c = [Number(it.lat), Number(it.lng)];
        pts.push(c);
        const kind = it.type === "meal" ? "meal" : it.type === "stay" ? "stay" : "place";
        const inner = kind === "meal" ? ico("ico-fork") : kind === "stay" ? "숙" : String(i + 1);
        const size = kind === "meal" ? 20 : 26;
        L.marker(c, { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk ${kind}" style="--c:${color}">${inner}</span>`, iconSize: [size, size], iconAnchor: [size / 2, size / 2] }), title: `${o.day}일차 ${i + 1}. ${it.name}`, keyboard: false }).addTo(RE.layer);
      });
      if (pts.length > 1) L.polyline(pts, { color, weight: hot ? 5 : 3, opacity: hot ? 1 : 0.8, dashArray: "6 7", interactive: false }).addTo(RE.layer);
      all.push(...pts);
    });
    const hp = homePoints(state.plan);
    [hp.st, hp.en].forEach((pt, k) => { if (pt && pt.lat != null && !(k === 1 && hp.st && hp.st.lat === pt.lat && hp.st.lng === pt.lng)) L.marker([Number(pt.lat), Number(pt.lng)], { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk home" style="--c:${dayColor(1)}">${ico("ico-home")}</span>`, iconSize: [24, 24], iconAnchor: [12, 12] }), title: pt.name, keyboard: false }).addTo(RE.layer); });
    if (fit && all.length) RE.map.fitBounds(L.latLngBounds(all), { padding: [24, 24], maxZoom: 14, animate: false });
  }
  const reDirtyDays = () => Object.keys(RE.dirty).map(Number).sort((a, b) => a - b);
  function reIcon(it) { return it.type === "meal" ? ico("ico-fork") : it.type === "stay" ? `<span class="re-k">숙</span>` : ""; }
  function renderRouteEditor() {
    const el = $("#re-content");
    if (!el || !RE.open) return;
    const fk = document.activeElement && el.contains(document.activeElement) ? (document.activeElement.dataset.k || null) : null;
    const hp = homePoints(state.plan);
    const nDays = RE.days.length;
    const dirty = reDirtyDays();
    const dayOpts = (cur) => RE.days.filter((x) => x.day !== cur).map((x) => `<option value="${x.day}">${x.day}일차로</option>`).join("");
    const days = RE.days.map((d, di) => {
      const color = dayColor(d.day);
      const isDirty = !!RE.dirty[d.day];
      let n = 0;
      const rows = d.items.map((it, i) => {
        const pinned = it.type === "stay";
        if (it.type !== "meal") n += 0;
        const op = RE.origPos[it.pid];
        const mealHint = it.type === "meal" && op && (op.day !== d.day || Math.abs(op.i - i) >= 2);
        const travel = !isDirty && it.inMin != null && i > 0 ? `<li class="re-gap" aria-hidden="true">↓ 약 ${fmtMin(it.inMin)}</li>` : "";
        return `${travel}<li class="re-item${pinned ? " pinned" : ""}${it.type === "meal" ? " meal" : ""}" data-pid="${esc(it.pid)}" data-day="${d.day}">
          <button type="button" class="re-grip" data-k="grip-${esc(it.pid)}" ${pinned ? "disabled aria-disabled=\"true\" title=\"숙소는 그날 마지막에 고정돼요\"" : `aria-label="${esc(it.name)} 옮기기. 위아래 화살표로 순서를 바꿔요" title="끌어서 옮기기 · ↑↓ 키"`}>${ico("ico-grip")}</button>
          <span class="re-num" style="--c:${color}">${i + 1}</span>
          <span class="re-name">${reIcon(it)}<b>${esc(it.name)}</b>${mealHint ? `<small class="re-hint">식사 시간이 바뀔 수 있어요</small>` : ""}</span>
          <span class="re-time">${isDirty ? "" : esc(it.arrive)}</span>
          ${pinned || nDays < 2 ? "" : `<label class="sr" for="rs-${esc(it.pid)}">${esc(it.name)} 다른 날로</label><select id="rs-${esc(it.pid)}" class="sel xs re-day-sel" data-pid="${esc(it.pid)}" data-k="sel-${esc(it.pid)}"><option value="">다른 날로</option>${dayOpts(d.day)}</select>`}
        </li>`;
      }).join("");
      const startRow = di === 0 && hp.st ? `<li class="re-item home pinned"><span class="re-grip-ph" aria-hidden="true">${ico("ico-home")}</span><span class="re-name"><b>${esc(hp.st.name)} 출발</b></span><span class="re-time">${esc(hp.st.time || "")}</span></li>` : "";
      const endRow = di === nDays - 1 && hp.en ? `<li class="re-item home pinned"><span class="re-grip-ph" aria-hidden="true">${ico("ico-home")}</span><span class="re-name"><b>${esc(hp.en.name)} 도착</b></span><span class="re-time">${esc(hp.en.time || "")}</span></li>` : "";
      return `<section class="re-day${RE.focusDay === d.day ? " focus" : ""}" data-day="${d.day}" style="--day:${color}">
        <h3><span class="day-no"><b>${d.day}</b>일차</span><span class="re-dh">${fmtDate(d.date)}</span>
          ${isDirty ? `<span class="badge user">순서 바뀜 · 저장하면 다시 계산</span>` : `<span class="muted">이동 약 ${fmtMin(d.travel)} · ${esc(km(d.dist || 0))}</span>`}</h3>
        <ol class="re-list" data-day="${d.day}">${startRow}${rows || `<li class="re-empty">여기로 끌어 놓을 수 있어요</li>`}${endRow}</ol></section>`;
    }).join("");
    el.innerHTML = `<div class="re-head"><h2 id="re-h" tabindex="-1">동선 편집</h2>
        <p class="muted">${ico("ico-grip")} 손잡이를 끌어 다른 날로도 옮길 수 있어요(↑↓ 키·‘다른 날로’도 돼요). 저장하면 이동시간을 다시 계산해요.</p>
        <div class="re-head-act"><button type="button" class="btn sm ghost" data-act="re-undo" data-k="undo"${state.undoCount > 0 && !state.busy ? "" : " disabled"} title="서버에 적용된 직전 수정을 되돌려요">${ico("ico-undo")}되돌리기${state.undoCount ? ` (${state.undoCount})` : ""}</button>
        <button type="button" class="x" data-act="re-close" data-k="close" aria-label="닫기 (Esc)">×</button></div></div>
      <div class="re-body">
        <div class="re-opt" aria-live="polite">${optimizeHtml()}</div>
        <div class="re-days">${days}</div>
      </div>
      <div class="re-foot">
        ${dirty.length ? `<button type="button" class="btn sm ghost" data-act="re-reset" data-k="reset">처음 순서로</button>` : ""}
        <span class="re-foot-sp"></span>
        <button type="button" class="btn sm ghost" data-act="re-cancel" data-k="cancel">취소</button>
        <button type="button" class="btn sm primary" data-act="re-save" data-k="save"${dirty.length && !state.busy ? "" : " disabled"}>${dirty.length ? `저장 (${dirty.map((x) => x + "일차").join("·")} 변경)` : "저장"}</button></div>`;
    if (fk) { const f = el.querySelector(`[data-k="${cssEsc(fk)}"]`); if (f) f.focus(); }
  }
  function optimizeHtml() {
    const O = RE.opt;
    const multi = RE.days.length > 1;
    const seg = multi ? `<div class="mode-seg re-scope" role="group" aria-label="최적화 범위">
        <button type="button" data-act="re-scope" data-v="all" data-k="scope-all" aria-pressed="${RE.scope === "all"}">전체 일정</button>
        <button type="button" data-act="re-scope" data-v="day" data-k="scope-day" aria-pressed="${RE.scope !== "all"}">일별</button></div>` : "";
    const head = (right) => `<div class="re-opt-h"><b>${ico("ico-route")}동선 최적화</b>${seg}${right}</div>`;
    if (RE.optErr) return head("") + `<p class="re-opt-msg">${esc(RE.optErr)}</p>`;
    if (!O) return head("") + `<p class="re-opt-msg">동선 최적화 결과를 계산하는 중이에요…</p>`;
    const total = Number(O.total_saved_min) || 0;
    const chip = (x) => x.from_day ? ` <span class="re-from" style="--c:${dayColor(x.from_day)}">←${esc(x.from_day)}일차</span>` : "";
    const ord = (arr) => (arr || []).map((x, i) => `<li>${i + 1}. ${esc(x.name)}${chip(x)}</li>`).join("");
    if (O.scope === "all") {
      const tb = Number(O.total_before_min) || 0, ta = Number(O.total_after_min) || 0;
      const saved = Math.max(0, tb - ta) || total;
      const moved = O.moved || [];
      return head("") + `<p class="re-opt-total">전체 일정 <b>${fmtMin(tb)} · ${esc(km(O.total_before_km))}</b> → <b>${fmtMin(ta)} · ${esc(km(O.total_after_km))}</b> ${saved > 0 ? `<b class="re-save">(−${fmtMin(saved)})</b>` : `<span class="badge ok">이미 최적이에요</span>`}</p>
        ${moved.length ? `<div class="re-moved"><p class="re-moved-h">날짜가 바뀌는 곳</p><ul>${moved.map((m) => `<li><b>${esc(m.name)}</b> <span class="re-dchip" style="--c:${dayColor(m.from_day)}">${esc(m.from_day)}일차</span> → <span class="re-dchip" style="--c:${dayColor(m.to_day)}">${esc(m.to_day)}일차</span></li>`).join("")}</ul></div>` : ""}
        <details class="re-cmp" open><summary>날짜별 새 순서</summary><div class="re-cmp-days">${(O.days || []).map((d) => `<div style="--day:${dayColor(d.day)}"><p><span class="day-no"><b>${esc(d.day)}</b>일차</span> ${fmtMin(d.before_min)} → ${fmtMin(d.after_min)}</p><ol>${ord(d.after)}</ol></div>`).join("")}</div></details>
        <div class="re-opt-go"><button type="button" class="btn sm primary" data-act="re-opt-scope-all" data-k="opt-scope-all"${saved > 0 && !state.busy ? "" : " disabled"}>전체 일정에 적용</button></div>
        ${O.note ? `<p class="re-opt-msg">${esc(O.note)}</p>` : ""}`;
    }
    const fell = RE.scope === "all" && O.scope && O.scope !== "all";
    return head(`<span>${total > 0 ? `날짜는 그대로, 약 <b>${fmtMin(total)}</b> 줄일 수 있어요` : "모든 날이 이미 최적이에요"}</span>
        <button type="button" class="btn sm primary" data-act="re-opt-all" data-k="opt-all"${total > 0 && !state.busy ? "" : " disabled"}>전체 적용</button>`)
      + (fell ? `<p class="re-opt-msg">날짜를 옮겨도 더 나아지지 않아 날짜별 순서만 정리했어요.</p>` : "") + `
      <ul class="re-opt-days">${(O.days || []).map((d) => {
        const saved = Number(d.saved_min) || 0;
        return `<li class="re-opt-day" style="--day:${dayColor(d.day)}"><span class="day-no"><b>${esc(d.day)}</b>일차</span>
          <span class="re-opt-nums">지금 ${fmtMin(d.before_min)} · ${esc(km(d.before_km))} → 최적화 ${fmtMin(d.after_min)} · ${esc(km(d.after_km))} ${saved > 0 ? `<b class="re-save">(−${fmtMin(saved)})</b>` : `<span class="badge ok">이미 최적이에요</span>`}</span>
          ${saved > 0 ? `<button type="button" class="btn xs ghost" data-act="re-opt-day" data-day="${esc(d.day)}" data-k="opt-${esc(d.day)}"${state.busy ? " disabled" : ""}>이 날 적용</button>` : ""}
          ${d.user_ordered && saved > 0 ? `<p class="re-opt-note">직접 정한 순서예요 — 적용하면 최적화 순서로 바뀌어요</p>` : ""}
          ${saved > 0 ? `<details class="re-cmp"><summary>순서 비교</summary><div class="re-cmp-b"><div><p>지금</p><ol>${ord(d.before)}</ol></div><div><p>최적화</p><ol>${ord(d.after)}</ol></div></div></details>` : ""}</li>`;
      }).join("")}</ul>${O.note ? `<p class="re-opt-msg">${esc(O.note)}</p>` : ""}`;
  }
  async function loadOptimize() {
    RE.opt = null; RE.optErr = "";
    let j = null;
    try {
      const r = await fetch(DEMO ? "/static/sample_optimize.json" : `/route/optimize?session_id=${encodeURIComponent(state.sessionId || "")}&scope=${RE.scope === "all" ? "all" : "day"}`);
      j = await r.json().catch(() => null);
      if (!r.ok || !j) throw new Error((j && (j.answer || j.message)) || "");
    } catch (e) { RE.optErr = (e && e.message) || "동선 최적화 결과를 불러오지 못했어요. 끌어서 직접 바꿀 수는 있어요."; }
    if (DEMO && j && RE.scope === "all" && j.all) j = j.all;
    RE.opt = j;
    const box = $("#re-panel .re-opt");
    if (box && RE.open) box.innerHTML = optimizeHtml();
  }
  function applyOptimize(day, scope) {
    const edit = { op: "optimize" };
    if (scope === "all") edit.scope = "all";
    else if (day) edit.day = day;
    closeRouteEditor();
    send({ question: scope === "all" ? "전체 일정 동선 최적화(날짜 이동 포함)" : day ? `${day}일차 동선 최적화` : "날짜별 동선 최적화", action: "edit", edit }, "edit");
  }
  function saveRouteEditor() {
    const dirty = reDirtyDays();
    if (!dirty.length) return;
    const days = dirty.map((n) => ({ day: n, place_ids: (RE.days.find((d) => d.day === n) || { items: [] }).items.map((it) => it.pid) }));
    closeRouteEditor();
    send({ question: `[동선 편집] ${dirty.map((x) => x + "일차").join("·")} 순서 바꾸기`, action: "edit", edit: { op: "order", days } }, "edit");
  }
  /* RE 상태 조작 */
  function reFind(pid) { for (const d of RE.days) { const i = d.items.findIndex((x) => x.pid === pid); if (i >= 0) return { d, i }; } return null; }
  function reInsertAt(d, it, idx) {
    // 숙소는 늘 마지막
    const stayIdx = d.items.findIndex((x) => x.type === "stay");
    if (it.type !== "stay" && stayIdx >= 0 && idx > stayIdx) idx = stayIdx;
    d.items.splice(Math.max(0, Math.min(idx, d.items.length)), 0, it);
  }
  function reMove(pid, toDay, toIdx) {
    const f = reFind(pid);
    if (!f || f.d.items[f.i].type === "stay") return;
    const it = f.d.items.splice(f.i, 1)[0];
    const td = RE.days.find((d) => d.day === toDay) || f.d;
    if (td === f.d && toIdx > f.i) toIdx -= 0;
    reInsertAt(td, it, toIdx);
    RE.dirty[f.d.day] = true; RE.dirty[td.day] = true;
    // 처음 순서와 같아지면 '바뀜' 해제
    RE.days.forEach((d) => { const o = RE.orig.find((x) => x.day === d.day); if (o && o.items.map((x) => x.pid).join() === d.items.map((x) => x.pid).join()) delete RE.dirty[d.day]; });
  }
  function reMoveToDay(pid, day) {
    const td = RE.days.find((d) => d.day === day);
    if (!td) return;
    reMove(pid, day, td.items.length);
    renderRouteEditor(); previewRoute();
    const g = $(`#re-panel .re-item[data-pid="${cssEsc(pid)}"] .re-grip`); if (g) { g.focus(); g.scrollIntoView({ block: "nearest" }); }
    toast(`${day}일차로 옮겼어요. 저장하면 반영돼요.`);
  }
  function reKeydown(e) {
    const g = e.target.closest && e.target.closest(".re-grip");
    if (e.key === "Escape") { e.preventDefault(); closeRouteEditor(); return; }
    if (!g || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    const pid = g.closest(".re-item").dataset.pid;
    const f = reFind(pid);
    if (!f) return;
    const ni = f.i + (e.key === "ArrowUp" ? -1 : 1);
    if (ni < 0 || ni >= f.d.items.length || f.d.items[ni].type === "stay") return;
    reMove(pid, f.d.day, ni);
    renderRouteEditor(); previewRoute();
    const ng = $(`#re-panel .re-item[data-pid="${cssEsc(pid)}"] .re-grip`); if (ng) ng.focus();
  }
  /* 끌어서 놓기 (pointer events) */
  let DRG = null;
  function reDragStart(e) {
    const g = e.target.closest(".re-grip");
    if (!g || g.disabled || e.button > 0) return;
    const row = g.closest(".re-item");
    e.preventDefault();
    const r = row.getBoundingClientRect();
    const ghost = row.cloneNode(true);
    ghost.classList.add("re-ghost"); ghost.style.width = r.width + "px"; ghost.style.left = r.left + "px"; ghost.style.top = r.top + "px";
    $("#re-panel").appendChild(ghost);
    row.classList.add("re-placeholder");
    DRG = { row, ghost, dy: e.clientY - r.top, pid: row.dataset.pid, raf: 0 };
    document.addEventListener("pointermove", reDragMove);
    document.addEventListener("pointerup", reDragEnd, { once: true });
    document.addEventListener("pointercancel", reDragEnd, { once: true });
  }
  function reDragMove(e) {
    if (!DRG) return;
    DRG.ghost.style.top = (e.clientY - DRG.dy) + "px";
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const list = under && under.closest && under.closest(".re-list");
    if (list) {
      const items = $$(".re-item:not(.re-placeholder)", list).filter((x) => !x.classList.contains("home"));
      let before = null;
      for (const it of items) { const rr = it.getBoundingClientRect(); if (e.clientY < rr.top + rr.height / 2) { before = it; break; } }
      const stay = items.find((x) => x.classList.contains("pinned"));
      if (!before && stay) before = stay;
      if (before && before.classList.contains("pinned") && !before.classList.contains("home")) { /* 숙소 앞 */ }
      const endHome = $(".re-item.home:last-child", list);
      if (before) list.insertBefore(DRG.row, before);
      else if (endHome && endHome !== list.firstElementChild) list.insertBefore(DRG.row, endHome);
      else list.appendChild(DRG.row);
      const empty = $(".re-empty", list); if (empty) empty.remove();
      if (!DRG.raf) DRG.raf = requestAnimationFrame(() => { DRG && (DRG.raf = 0); previewFromDom(Number(list.dataset.day)); });
    }
    const body = $("#re-panel .re-body");
    if (body) { const br = body.getBoundingClientRect(); if (e.clientY < br.top + 40) body.scrollTop -= 12; else if (e.clientY > br.bottom - 40) body.scrollTop += 12; }
  }
  function domOrder() {
    return $$("#re-panel .re-list").map((l) => ({ day: Number(l.dataset.day), pids: $$(".re-item[data-pid]", l).map((x) => x.dataset.pid) }));
  }
  function applyDomOrder() {
    const byPid = {};
    RE.days.forEach((d) => d.items.forEach((it) => { byPid[it.pid] = it; }));
    domOrder().forEach(({ day, pids }) => {
      const d = RE.days.find((x) => x.day === day);
      if (!d) return;
      const items = pids.map((p) => byPid[p]).filter(Boolean);
      const stays = items.filter((x) => x.type === "stay");
      d.items = items.filter((x) => x.type !== "stay").concat(stays);
    });
    RE.days.forEach((d) => { const o = RE.orig.find((x) => x.day === d.day); if (o && o.items.map((x) => x.pid).join() !== d.items.map((x) => x.pid).join()) RE.dirty[d.day] = true; else delete RE.dirty[d.day]; });
  }
  function reDragEnd() {
    document.removeEventListener("pointermove", reDragMove);
    if (!DRG) return;
    const pid = DRG.pid;
    DRG.ghost.remove(); DRG.row.classList.remove("re-placeholder");
    DRG = null;
    applyDomOrder();
    renderRouteEditor(); previewRoute();
    const g = $(`#re-panel .re-item[data-pid="${cssEsc(pid)}"] .re-grip`); if (g) g.focus();
  }
  /* 지도 미리보기: 편집 중인 순서로 번호와 선을 다시 그린다 */
  function previewPlan(order) {
    const plan = JSON.parse(JSON.stringify(state.plan));
    const P = placeIndex(state.plan);
    const all = {};
    (state.plan.itinerary || []).forEach((d) => (d.stops || []).forEach((s) => { if (s.place_id != null) all[String(s.place_id)] = s; }));
    plan.itinerary.forEach((d) => {
      const o = order.find((x) => x.day === d.day);
      if (!o) return;
      d.stops = o.pids.map((pid, i) => Object.assign({}, all[pid], { order: i + 1 }));
      d.legs = [];
    });
    return plan;
  }
  function previewFromDom(day) {
    if (!RE.open) return;
    RE.dragOrder = domOrder(); RE.hotDay = day || null;
    drawReMap(false);
  }
  function previewRoute() {
    if (!RE.open) return;
    RE.dragOrder = null; RE.hotDay = null;
    drawReMap(false);
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
    if (t.classList && t.classList.contains("re-day-sel")) { if (t.value) reMoveToDay(t.dataset.pid, Number(t.value)); return; }
    if (t.classList && t.classList.contains("move-day-sel")) { if (t.value) tlMoveToDay(t.dataset.pid, Number(t.value)); return; }
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
        </nav>${draft ? `<p class="muted small">아직 확정 전 초안이에요. 상담 화면에서 확정하면 공유 링크가 고정돼요.</p>` : ""}
        ${state.owner && state.mode !== "edit" ? `<button type="button" class="btn sm ghost" data-act="mode" data-v="edit">✏️ 이 계획 고치기</button>` : ""}${reconfirmBanner()}`;
    } else if (READ_ONLY) {
      const id = esc(BOOT.planId);
      act = `<nav class="exports" aria-label="공유"><button type="button" class="btn sm ghost" data-act="share-link">링크 공유</button></nav>` + exportLinks({ html_url: `/plan/${id}.html`, docx_url: `/plan/${id}.docx`, print_url: `/plan/${id}?print=1`, ics_url: `/plan/${id}.ics`, report_url: `/plan/${id}/report` });
    } else {
      // 상담 화면: 한 줄짜리 확정 막대(오른쪽 정보창 아래에 고정, 모바일은 입력창 위에 고정)
      const fv = fullViewUrl();
      const busy = state.busy ? " disabled" : "";
      const lbl = (long, short) => `<span class="ab-l">${long}</span><span class="ab-s">${short}</span>`;
      const np = pendingCount();
      let main = "", note = "";
      if (np) {
        main = `<button type="button" class="btn primary" data-act="cond-apply"${busy} title="조건을 바꿨어요 — 다시 세운 뒤 확정할 수 있어요">${lbl(`변경 ${np}건 · 계획 다시 세우기`, `다시 세우기 (${np})`)}</button>`;
      } else if (state.needsReconfirm) {
        main = `<button type="button" class="btn primary" data-act="reconfirm"${busy} title="확정 후 수정됨 · 공유 링크는 그대로예요">${lbl("수정한 계획으로 다시 확정", "다시 확정")}</button>`;
      } else if (state.planStatus === "확정" && state.exports) {
        main = `${state.exports.share_url ? `<a class="btn primary" href="${esc(state.exports.share_url)}" target="_blank" rel="noopener">${lbl("공유 링크 열기", "공유")}</a>` : ""}
          <details class="ab-more"><summary class="btn ghost">${lbl("내려받기·내보내기", "내보내기")}</summary><div class="ab-menu">${exportLinks(state.exports)}</div></details>`;
      } else if (state.planStatus === "승인 대기") {
        const pg = getProgress();
        const full = Number(pg.percent) >= 100;
        const na = (pg.assumed || []).length, nm = (pg.missing || []).length;
        const left = [na ? `가정 ${na}` : "", nm ? `미정 ${nm}` : ""].filter(Boolean).join(" · ");
        if (!full && left) note = `<span class="ab-note" title="${esc(left)}이(가) 남아 있어요 · 그대로 확정해도 괜찮아요">${esc(left)}</span>`;
        main = `<button type="button" class="btn primary" data-act="approve"${busy} title="확정하면 공유 링크·HTML·Word·PDF·캘린더를 만들어요. 확정 전에는 저장·공유하지 않아요.">${lbl(full ? "이 계획으로 확정할게요" : "지금 조건 그대로 확정", "확정하기")}</button>`;
      }
      act = `<div class="actbar">${statusPill()}${note}<span class="ab-sp"></span>${main}
        ${fv ? `<a class="ab-fv" href="${esc(fv)}" target="_blank" rel="noopener" title="전체 화면으로 보기"><span aria-hidden="true">⛶</span>${lbl("전체 화면으로 보기", "전체 화면")}</a>` : ""}</div>`;
      el.classList.add("is-bar");
    }
    if (VIEW || READ_ONLY) el.classList.remove("is-bar");
    el.innerHTML = totals + act;
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
    setBaseLayer(MAPL.want || "osm", true);
    ensureMapCtrl();
    M.map.setView([36.3, 127.8], 7);
    return M.map;
  }
  /* ───────── 지도 종류 고르기 · 크게 보기 ───────── */
  const OSM_LAYER = { id: "osm", label: "OpenStreetMap", url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", attribution: "© OpenStreetMap contributors", max_zoom: 19 };
  const MAPL = { list: [OSM_LAYER], ext3d: null, cur: null, want: null, base: null, over: null, failed: {} };
  async function loadLayers() {
    let j = null;
    try { const r = await fetch(DEMO ? "/static/sample_layers.json" : "/map/layers"); if (r.ok) j = await r.json(); } catch (e) { j = null; }
    const list = (j && Array.isArray(j.layers) && j.layers.length) ? j.layers.filter((l) => l && l.id && l.url) : [];
    MAPL.list = list.some((l) => l.id === "osm") ? list : [OSM_LAYER].concat(list);
    MAPL.ext3d = j && j.external_3d && /^https:\/\//i.test(j.external_3d.url || "") ? j.external_3d : null;
    let saved = null;
    try { saved = localStorage.getItem("momo.mapLayer"); } catch (e) { saved = null; }
    MAPL.want = [saved, j && j.default, "osm"].find((id) => id && MAPL.list.some((l) => l.id === id));
    if (M.map) setBaseLayer(MAPL.want, true);
    renderMapCtrl();
  }
  function setBaseLayer(id, quiet, fallback) {
    if (!M.map || !window.L) { MAPL.want = id; return; }
    const lay = MAPL.list.find((l) => l.id === id) || MAPL.list[0];
    if (MAPL.base) M.map.removeLayer(MAPL.base);
    if (MAPL.over) { M.map.removeLayer(MAPL.over); MAPL.over = null; }
    const opt = (l) => ({ attribution: l.attribution || "", maxZoom: l.max_zoom || 19, minZoom: l.min_zoom || 0, subdomains: "abc" });
    MAPL.base = L.tileLayer(lay.url, opt(lay));
    const vw = /^vw/.test(lay.id);
    if (vw) MAPL.base.on("tileerror", () => onLayerError(lay.id));
    MAPL.base.addTo(M.map);
    if (MAPL.base.bringToBack) MAPL.base.bringToBack();
    if (lay.overlay_url) { MAPL.over = L.tileLayer(lay.overlay_url, Object.assign(opt(lay), { attribution: "" })); if (vw) MAPL.over.on("tileerror", () => onLayerError(lay.id)); MAPL.over.addTo(M.map); }
    MAPL.cur = lay.id;
    M.map.getContainer().classList.toggle("map-dark", /sat|hybrid|night/.test(lay.id));
    M.map.getContainer().classList.toggle("map-vw", vw);
    if (!quiet && !fallback) { try { localStorage.setItem("momo.mapLayer", lay.id); } catch (e) { /* 저장 불가 */ } }
    renderMapCtrl();
  }
  function onLayerError(id) {
    if (MAPL.cur !== id || MAPL.failed[id]) return;
    MAPL.failed[id] = true;
    setBaseLayer("osm", true, true);
    toast("브이월드 지도를 불러오지 못해 OpenStreetMap으로 바꿨어요");
  }
  function ensureMapCtrl() {
    const box = $("#map-panel .map-box");
    if (!box || $(".map-ctrl", box)) return;
    const c = document.createElement("div");
    c.className = "map-ctrl";
    box.appendChild(c);
    renderMapCtrl();
  }
  function renderMapCtrl() {
    const c = $("#map-panel .map-ctrl");
    if (!c) return;
    const cur = MAPL.list.find((l) => l.id === MAPL.cur) || MAPL.list[0];
    const open = c.dataset.open === "1";
    const full = $("#map-panel") && $("#map-panel").classList.contains("map-full");
    c.innerHTML = `<div class="mc-layer">
        <button type="button" class="mc-btn" data-act="map-layer-menu" aria-haspopup="menu" aria-expanded="${open}" aria-controls="mc-menu" title="지도 종류">${ico("ico-layers")}<span>${esc(cur ? cur.label : "지도")}</span></button>
        ${open ? `<ul id="mc-menu" class="mc-menu" role="menu" aria-label="지도 종류">${MAPL.list.map((l) => `<li role="none"><button type="button" role="menuitemradio" aria-checked="${l.id === MAPL.cur}" data-act="map-layer" data-id="${esc(l.id)}">${esc(l.label)}${MAPL.failed[l.id] ? ` <small>(불러오기 실패)</small>` : ""}</button></li>`).join("")}
          ${MAPL.ext3d ? `<li role="none" class="mc-3d"><a role="menuitem" href="${esc(MAPL.ext3d.url)}" target="_blank" rel="noopener">${esc(MAPL.ext3d.label || "3D로 보기")} ↗</a></li>` : ""}</ul>` : ""}
      </div>
      ${MAPL.ext3d ? `<a class="mc-btn mc-3dlink" href="${esc(MAPL.ext3d.url)}" target="_blank" rel="noopener" title="${esc(MAPL.ext3d.label || "3D로 보기")}">3D ↗</a>` : ""}
      <button type="button" class="mc-btn mc-full" data-act="map-full" aria-pressed="${!!full}" title="${full ? "작게 보기(Esc)" : "지도 크게 보기"}">${ico(full ? "ico-shrink" : "ico-expand")}<span>${full ? "닫기" : "크게 보기"}</span></button>`;
  }
  const MF = { parent: null, next: null, last: null };
  function toggleMapFull(on) {
    const panel = $("#map-panel");
    if (!panel) return;
    const isOn = panel.classList.contains("map-full");
    if (on == null) on = !isOn;
    if (on === isOn) return;
    const view = M.map ? { c: M.map.getCenter(), z: M.map.getZoom() } : null;
    if (on) {
      MF.parent = panel.parentNode; MF.next = panel.nextSibling; MF.last = document.activeElement;
      document.body.appendChild(panel); // 컨테이너 쿼리·sticky 조상 밖으로 꺼내야 화면 전체를 덮는다
      panel.classList.add("map-full");
      panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "true"); panel.setAttribute("aria-label", "지도 크게 보기");
      document.body.classList.add("map-full-open");
    } else {
      panel.classList.remove("map-full");
      panel.removeAttribute("role"); panel.removeAttribute("aria-modal"); panel.removeAttribute("aria-label");
      if (MF.parent && MF.parent.isConnected) MF.parent.insertBefore(panel, MF.next && MF.next.parentNode === MF.parent ? MF.next : null);
      else placePanels();
      document.body.classList.remove("map-full-open");
    }
    renderMapCtrl();
    setTimeout(() => {
      if (M.map) { M.map.invalidateSize(); if (view) M.map.setView(view.c, view.z, { animate: false }); }
      const b = $("#map-panel .mc-full");
      if (on && b) b.focus();
      else if (!on && MF.last && MF.last.isConnected) MF.last.focus();
    }, 60);
  }

  function coordOf(s, P) {
    if (s.lat != null && s.lng != null) return [Number(s.lat), Number(s.lng)];
    const p = s.place_id != null ? P.get(String(s.place_id)) : null;
    return p && p.lat != null ? [Number(p.lat), Number(p.lng)] : null;
  }
  function renderMap(override) {
    const plan = override || state.plan;
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
    const rp = isDrive(tg) && Array.isArray(tg.route_path) && tg.route_path.length > 1
      ? tg.route_path.map((q) => [Number(q[0]), Number(q[1])]).filter((q) => isFinite(q[0]) && isFinite(q[1])) : null;
    const hp = homePoints(plan);
    const its = plan.itinerary || [];
    const firstC = (() => { const d0 = its[0]; for (const st of (d0 && d0.stops) || []) { const c = coordOf(st, P); if (c) return c; } return null; })();
    const lastC = (() => { const dl = its[its.length - 1]; const ss = ((dl && dl.stops) || []).slice().reverse(); for (const st of ss) { const c = coordOf(st, P); if (c) return c; } return null; })();
    const rg = L.layerGroup(); const rb = [];
    const homeMk = (pt, key, color, label) => {
      const m = L.marker([Number(pt.lat), Number(pt.lng)], { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk home" style="--c:${color}">${ico("ico-home")}</span>`, iconSize: [26, 26], iconAnchor: [13, 13] }), title: label, alt: label, riseOnHover: true }).addTo(rg);
      m.on("click", () => focusCard(`stop-${key}`)); M.markers[key] = m; return m;
    };
    const sPt = hp.st && hp.st.lat != null ? [Number(hp.st.lat), Number(hp.st.lng)] : (rp ? rp[0] : null);
    if (sPt) {
      homeMk({ lat: sPt[0], lng: sPt[1] }, "home-s", dayColor(1), `출발 · ${(hp.st && hp.st.name) || "출발지"}`); rb.push(sPt);
      if (rp) { L.polyline(rp, { color: dayColor(1), weight: 3, opacity: 0.6, interactive: false }).addTo(rg); rb.push(...rp); }
      else if (firstC) { L.polyline([sPt, firstC], { color: dayColor(1), weight: 2, opacity: 0.7, dashArray: "2 8", interactive: false }).addTo(rg); rb.push(firstC); }
    }
    if (hp.en && hp.en.lat != null) {
      const ePt = [Number(hp.en.lat), Number(hp.en.lng)];
      const same = sPt && Math.abs(sPt[0] - ePt[0]) < 0.003 && Math.abs(sPt[1] - ePt[1]) < 0.003;
      const lastColor = dayColor(its.length ? its[its.length - 1].day : 1);
      if (same) M.markers["home-e"] = M.markers["home-s"];
      else homeMk(hp.en, "home-e", lastColor, `도착 · ${hp.en.name || "도착지"}`);
      rb.push(ePt);
      if (!(rp && same) && lastC) { L.polyline([lastC, ePt], { color: lastColor, weight: 2, opacity: 0.7, dashArray: "2 8", interactive: false }).addTo(rg); rb.push(lastC); }
    }
    if (rb.length) { M.route = rg; M.routeBounds = L.latLngBounds(rb); } else state.routeFit = false;
    if (M.roadEv) { map.removeLayer(M.roadEv); M.roadEv = null; }
    const soAdded = ((plan.return_stopovers && plan.return_stopovers.items) || []).filter((x) => x.added && x.lat != null);
    if (soAdded.length) {
      M.roadEv = L.layerGroup();
      soAdded.forEach((x, i) => L.marker([Number(x.lat), Number(x.lng)], { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk so" style="--c:${dayColor(lastDayNo())}">${i + 1}</span>`, iconSize: [22, 22], iconAnchor: [11, 11] }), title: `돌아오는 길 들를 곳 · ${x.name}`, keyboard: false }).addTo(M.roadEv));
    }
    if (isDrive(tg) && ((tg.road_events || []).length || (tg.road_cautions || []).length)) {
      M.roadEv = M.roadEv || L.layerGroup();
      (tg.road_events || []).concat((tg.road_cautions || []).map((x) => Object.assign({ caution: true }, x))).forEach((x) => {
        if (x.lat == null || x.lng == null) return;
        L.marker([Number(x.lat), Number(x.lng)], { icon: L.divIcon({ className: "mk-wrap", html: `<span class="mk warnmk${x.caution ? " caution" : ""}">${ico("ico-alert")}</span>`, iconSize: [20, 20], iconAnchor: [10, 10] }),
          title: `${x.type || "알림"}${x.road ? " · " + x.road : ""}${x.message ? " — " + x.message : ""}`, keyboard: false }).addTo(M.roadEv);
      });
    }
    const lg = $("#map-panel .legend");
    if (lg) lg.textContent = "점선은 방문 순서(직선)예요. 실제 도로와 다를 수 있어요." + (rp ? " 실선은 자차 도로 경로(OSRM)예요." : "") + (M.route ? " 집 모양은 출발·도착지예요." : "");
    if (state.mapDay !== "all" && !M.groups[state.mapDay]) state.mapDay = "all";
    if (tabs) {
      tabs.innerHTML = [["all", "전체"]].concat(plan.itinerary.map((d) => [String(d.day), `${d.day}일차`])).map(([v, t]) =>
        `<button type="button" role="tab" class="tab${String(state.mapDay) === v && !state.routeFit ? " on" : ""}" aria-selected="${String(state.mapDay) === v && !state.routeFit}" data-act="mapday" data-v="${v}"${v !== "all" ? ` style="--c:${dayColor(v)}"` : ""}>${v !== "all" ? `<i class="dot" style="background:${dayColor(v)}"></i>` : ""}${t}</button>`).join("")
        + (M.route ? `<button type="button" class="tab route-tab${state.routeFit ? " on" : ""}" aria-pressed="${!!state.routeFit}" data-act="route-fit">${ico("ico-car")}가는 길 보기</button>` : "");
    }
    applyMapDay();
  }
  const lastDayNo = () => { const its = (state.plan && state.plan.itinerary) || []; return its.length ? its[its.length - 1].day : 1; };
  function applyMapDay() {
    if (!M.map) return;
    if (M.route) { if (state.mapDay === "all" || String(state.mapDay) === "1" || String(state.mapDay) === String(lastDayNo()) || state.routeFit) M.route.addTo(M.map); else M.map.removeLayer(M.route); }
    if (M.roadEv) { if (state.routeFit) M.roadEv.addTo(M.map); else M.map.removeLayer(M.roadEv); }
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
        highlightMarker(key, key.indexOf("home") !== 0);
      });
    }, { root, rootMargin: "-40% 0px -50% 0px", threshold: 0 });
    $$("#brochure .stop").forEach((s) => io.observe(s));
  }

  /* ───────── 배치: PC 오른쪽 패널 ↔ 모바일 접이식 ───────── */
  function stashPanels() {
    const side = $("#side");
    if (!side) return;
    ["#cond-panel", "#cost-panel", "#pack-panel", "#map-panel", "#act-panel"].forEach((s) => { const x = $(s); if (x && x.parentNode !== side && !x.classList.contains("map-full")) side.appendChild(x); });
  }
  function placePanels() {
    const pc = MQ_PC.matches;
    const side = $("#side");
    const cond = $("#cond-panel"), mapP0 = $("#map-panel"), mapP = mapP0 && mapP0.classList.contains("map-full") ? null : mapP0, cost = $("#cost-panel"), act = $("#act-panel"), pack = $("#pack-panel");
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
      if (ma) { if (cost && cost.parentNode !== ma) ma.appendChild(cost); if (pack && pack.parentNode !== ma) ma.appendChild(pack); }
      if (act && !READ_ONLY) { let bar = $("#m-actbar"); if (!bar) { bar = document.createElement("div"); bar.id = "m-actbar"; document.body.appendChild(bar); } if (act.parentNode !== bar) bar.appendChild(act); }
      else if (act && ma && act.parentNode !== ma) ma.appendChild(act);
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
        <h3 class="ti-title"><span>${esc(t.title || t.region || "이름 없는 여행")}</span><button type="button" class="title-pen" data-act="trip-rename" data-sid="${esc(t.session_id)}" data-title="${esc(t.title || "")}" aria-label="${esc(t.title || "이 여행")} 이름 바꾸기">✎</button></h3>
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
    Object.assign(state, { sessionId: null, draftVersion: null, status: null, stage: "intake", planStatus: null, plan: null, conditions: null, exports: null, openCond: null, mapDay: "all", progress: null, planNo: null, planDiff: null, diffHidden: false, diffForce: false, needsReconfirm: false, undoCount: 0, planId: null });
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

  /* 계획 버전별로 이전 대화 접기 — 지우지 않고 '펼치기'로 다시 본다 */
  const rowsExist = () => $$("#thread > .msg").length > 1;
  function foldThread() {
    const thread = $("#thread");
    if (!thread) return;
    const msgs = $$(":scope > .msg", thread).filter((m) => !m.classList.contains("is-loading"));
    if (msgs.length < 2) return;
    const planOf = (m) => m.dataset.plan || "";
    let lastP = "";
    for (let k = msgs.length - 1; k >= 0 && !lastP; k--) lastP = planOf(msgs[k]);
    let i = msgs.length - 1;
    while (i > 0 && (planOf(msgs[i - 1]) || lastP) === lastP) i--;
    const runs = [];
    msgs.slice(0, i).forEach((m) => {
      const pn = planOf(m) || "1";
      const r = runs[runs.length - 1];
      if (r && r.p === pn) r.items.push(m); else runs.push({ p: pn, items: [m] });
    });
    runs.forEach((r) => {
      const sec = document.createElement("section");
      sec.className = "fold";
      sec.dataset.plan = r.p;
      const n = r.items.length;
      sec.innerHTML = `<button type="button" class="fold-h" data-act="fold-toggle" aria-expanded="false"><span class="fold-v">이전 계획 v${esc(r.p)}</span><span class="fold-n">대화 ${n}개</span><span class="fold-x">펼치기</span>${ico("ico-chev", "ico chev")}</button><div class="fold-b" hidden></div>`;
      thread.insertBefore(sec, r.items[0]);
      const body = $(".fold-b", sec);
      r.items.forEach((m) => body.appendChild(m));
    });
  }
  function openFoldOf(el) {
    const f = el && el.closest(".fold");
    if (f && !f.classList.contains("open")) {
      f.classList.add("open"); $(".fold-b", f).hidden = false;
      const h = $(".fold-h", f); h.setAttribute("aria-expanded", "true"); $(".fold-x", h).textContent = "접기";
    }
  }
  async function fetchMessages(sid) {
    try {
      const r = await fetch(DEMO ? "/static/sample_messages.json" : `/trips/${encodeURIComponent(sid)}/messages?limit=300`);
      if (!r.ok) return null;
      const j = await r.json();
      return Array.isArray(j.messages) ? j.messages : null;
    } catch (e) { return null; }
  }
  function restoreThread(rows) {
    // user 줄은 바로 다음 모모 답의 계획 번호를 따른다(계획 전 접수 단계는 v1)
    let pending = [], lastNo = 1;
    rows.forEach((m) => {
      if (m.role === "user") {
        const el = bubble("me", m.text || "", { mid: m.id, time: m.created });
        if (el) { el.dataset.kind = m.kind || "chat"; pending.push(el); }
      } else {
        const no = m.plan_no != null ? Number(m.plan_no) : lastNo;
        lastNo = no;
        bubble("momo", m.text || "", { html: m.html || "", status: m.status, mid: m.id, time: m.created, plan: no, longPlan: !m.html });
        pending.forEach((el) => { el.dataset.plan = String(no); });
        pending = [];
      }
    });
    pending.forEach((el) => { el.dataset.plan = String(lastNo); });
    $$("#thread .replies").forEach((r) => r.remove());
  }
  const KIND_LABEL = { chat: "대화", start: "시작", edit: "편집", set: "조건", approve: "확정", cost: "비용" };
  async function openHistory() {
    const dlg = $("#hist");
    if (!dlg) return;
    const body = $("#hist-body", dlg);
    body.innerHTML = `<p class="muted">불러오는 중이에요…</p>`;
    if (dlg.showModal) { if (!dlg.open) dlg.showModal(); } else dlg.setAttribute("open", "");
    const rows = state.sessionId || DEMO ? await fetchMessages(state.sessionId) : [];
    state.history = (rows || []).filter((m) => m.role === "user").reverse();
    renderHistory();
    const q = $("#hist-q"); if (q) q.focus();
  }
  function renderHistory() {
    const list = $("#hist-list");
    if (!list) { const body = $("#hist-body"); if (body) body.innerHTML = `<label class="sr" for="hist-q">요청 찾기</label><input id="hist-q" type="search" placeholder="요청 찾기 (예: 카페)" autocomplete="off"><ul id="hist-list" class="hist-list"></ul>`; }
    const q = (($("#hist-q") || {}).value || "").trim();
    const items = (state.history || []).filter((m) => !q || String(m.text || "").indexOf(q) >= 0);
    const mark = (t) => q ? esc(t).split(esc(q)).join(`<mark>${esc(q)}</mark>`) : esc(t);
    $("#hist-list").innerHTML = items.length ? items.map((m) => `<li class="hist-item">
        <button type="button" class="hist-go" data-act="hist-go" data-mid="${esc(m.id)}" data-text="${esc(m.text || "")}">
          <span class="hist-meta"><span class="badge ${m.kind === "chat" ? "route" : "user"}">${esc(KIND_LABEL[m.kind] || "대화")}</span><time>${esc(fmtMsgTime(m.created))}</time></span>
          <span class="hist-t">${mark(m.text || "")}</span></button>
        <span class="hist-act"><button type="button" class="btn xs ghost" data-act="hist-resend" data-text="${esc(m.text || "")}">다시 보내기</button>
        <button type="button" class="btn xs ghost" data-act="hist-copy" data-text="${esc(m.text || "")}">복사</button></span></li>`).join("")
      : `<li class="muted hist-empty">${q ? `‘${esc(q)}’가 들어간 요청이 없어요.` : "아직 보낸 요청이 없어요."}</li>`;
  }
  function jumpToMessage(mid, text) {
    let el = mid ? $(`#thread .msg[data-mid="${cssEsc(mid)}"]`) : null;
    if (!el && text) el = $$("#thread .msg.me").reverse().find((x) => ($(".txt", x) || {}).textContent === text) || null;
    if (!el) { toast("대화 속에서 그 요청을 찾지 못했어요"); return; }
    openFoldOf(el);
    el.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
    el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1800);
  }
  async function chooseReplan(key) {
    const q = state.replanQ || state.lastQuestion || "";
    if (!q) return;
    if (DEMO) { toast("데모 모드에서는 바뀌지 않아요"); return; }
    if (key === "update") {
      state.replanUpdate = true;
      send({ question: q, replan: "update" }, "chat", "이 계획을 새 버전으로 고칠게요");
    } else {
      const old = state.sessionId;
      if (old && state.plan) Trips.remember(old);
      resetSession();
      Trips.setCurrent(null);
      S.collapsed = true;
      await send({ question: q, session_id: null, draft_version: null }, "chat", q);
      if (state.sessionId && state.sessionId !== old) { Trips.remember(state.sessionId); Trips.setCurrent(state.sessionId); toast("새 상담으로 시작했어요 · 이전 계획은 내 여행 계획에", 6000); }
    }
  }

  async function loadTrip(sid, auto) {
    let j = null;
    try { const r = await fetch("/trips/" + encodeURIComponent(sid)); if (r.ok) j = await r.json(); } catch (e) { j = null; }
    if (!j || !j.session_id) {
      if (auto) { Trips.setCurrent(null); return; }
      toast("그 계획을 찾지 못했어요. 목록에서 빼 둘게요."); Trips.forget(sid); openTrips(); return;
    }
    resetSession();
    state.sessionId = sid; loadMode();
    S.collapsed = true;
    const rows = await fetchMessages(sid);
    const st = j.status === "초안" ? "승인 대기" : j.status;
    if (rows && rows.length) {
      restoreThread(rows);
      const th = $("#thread"), br = $("#brochure"); if (th && br) th.appendChild(br);
      handleResponse(Object.assign({}, j, { status: st, quick_replies: [] }), "restore");
      foldThread();
      const lastMsg = $$("#thread > .msg").pop();
      if (lastMsg) requestAnimationFrame(() => lastMsg.scrollIntoView({ block: "start" }));
    } else {
      bubble("momo", auto ? "지난번에 보던 계획을 다시 펼쳐 놨어요." : "저장된 계획을 불러왔어요.");
      handleResponse(Object.assign({}, j, { status: st, quick_replies: [] }), "chat");
    }
    if (j.progress) { state.progress = j.progress; updateProgress(); renderActions(); }
  }

  /* ───────── 이벤트(위임) ───────── */
  function onClick(e) {
    if (POP.el && !POP.el.hidden && !e.target.closest("#mini-pop") && !e.target.closest('[data-act="stay-open"],[data-act="free-open"]')) popClose(false);
    const mc = $("#map-panel .map-ctrl");
    if (mc && mc.dataset.open === "1" && !e.target.closest(".mc-layer")) { mc.dataset.open = ""; renderMapCtrl(); }
    const pop = $("#tp-pop");
    if (pop && !pop.hidden && !e.target.closest("#tp-pop") && !e.target.closest(".tp-trigger") && !e.target.closest("#tp-back")) tpClose(false);
    const t = e.target.closest("[data-act]");
    if (!t) return;
    const act = t.dataset.act;
    const inToast = t.closest(".toast-item");
    if (inToast) { closeToastOf(inToast); if (act === "toast-close") return; }
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
      case "stay-open": if (canEdit()) popOpen(t, "stay", { pid: t.dataset.pid, name: t.dataset.name, orig: Number(t.dataset.min), min: Number(t.dataset.min), arrive: t.dataset.arrive }); break;
      case "stay-chip": { const D = POP.data, v = t.dataset.v; D.min = v[0] === "=" ? Number(v.slice(1)) : Math.max(15, Math.min(360, D.min + Number(v))); popRender(false); const f = POP.el.querySelector(`[data-act="stay-chip"][data-v="${v}"]`); if (f) f.focus(); break; }
      case "stay-apply": { const D = POP.data; popClose(false); send({ question: `${D.name} 머무는 시간 ${fmtMin(D.min)}로`, action: "edit", edit: { op: "stay", place_id: D.pid, minutes: D.min } }, "edit"); break; }
      case "free-open": if (canEdit()) popOpen(t, "free", { after: t.dataset.after || null, afterName: t.dataset.name || "", day: Number(t.dataset.day), label: "휴식", min: 45 }); break;
      case "free-label": { POP.data.label = t.dataset.v; popRender(false); const f = POP.el.querySelector(`[data-act="free-label"][data-v="${t.dataset.v}"]`); if (f) f.focus(); break; }
      case "free-min": { POP.data.min = Number(t.dataset.v); popRender(false); const f = POP.el.querySelector(`[data-act="free-min"][data-v="${t.dataset.v}"]`); if (f) f.focus(); break; }
      case "free-apply": {
        const D = POP.data; popClose(false);
        const edit = { op: "free", action: "add", minutes: D.min, label: D.label.trim().slice(0, 30) };
        if (D.after) edit.after = D.after; else edit.day = D.day;
        send({ question: `${D.afterName ? D.afterName + " 다음에 " : `${D.day}일차에 `}${edit.label} ${fmtMin(D.min)} 넣기`, action: "edit", edit }, "edit");
        break;
      }
      case "free-remove": if (canEdit()) send({ question: `개인 시간 ‘${t.dataset.name}’ 빼기`, action: "edit", edit: { op: "free", action: "remove", place_id: t.dataset.pid } }, "edit"); break;
      case "pop-close": popClose(true); break;
      case "title-edit": state.titleEdit = true; renderBrochure({}); { const i = $("#title-in"); if (i) { i.focus(); i.select(); } } break;
      case "title-cancel": state.titleEdit = false; renderBrochure({}); { const b = $(".title-pen"); if (b) b.focus(); } break;
      case "title-default": state.titleEdit = false; sendTitle(""); break;
      case "trip-rename": renameTrip(t); break;
      case "fold-toggle": {
        const f = t.closest(".fold"), on = !f.classList.contains("open");
        f.classList.toggle("open", on); $(".fold-b", f).hidden = !on; t.setAttribute("aria-expanded", String(on)); $(".fold-x", t).textContent = on ? "접기" : "펼치기";
        break;
      }
      case "hist-open": openHistory(); break;
      case "hist-close": { const d = $("#hist"); if (d) d.close ? d.close() : d.removeAttribute("open"); break; }
      case "hist-go": { const d = $("#hist"); if (d && d.close) d.close(); jumpToMessage(t.dataset.mid, t.dataset.text); break; }
      case "hist-resend": { const d = $("#hist"); if (d && d.close) d.close(); const q = $("#q"); if (q) { q.value = t.dataset.text; q.dispatchEvent(new Event("input")); q.focus(); } toast("입력창에 넣었어요. 확인하고 보내 주세요."); break; }
      case "hist-copy": { const tx = t.dataset.text; (navigator.clipboard ? navigator.clipboard.writeText(tx) : Promise.reject()).then(() => toast("복사했어요"), () => toast("복사하지 못했어요")); break; }
      case "replan": chooseReplan(t.dataset.k); break;
      case "diff-only": { state.diffOnly = !state.diffOnly; $("#brochure").classList.toggle("diff-only", state.diffOnly); t.setAttribute("aria-pressed", String(state.diffOnly)); break; }
      case "diff-hide": state.diffHidden = true; state.diffOnly = false; renderBrochure({}); break;
      case "bf-mode": if (canEdit()) send({ question: `아침 식사를 ${t.dataset.v}(으)로`, action: "edit", edit: { op: "breakfast", mode: t.dataset.v } }, "edit"); break;
      case "bf-skip": if (canEdit()) send({ question: `${t.dataset.day}일차 아침 식사 빼기`, action: "edit", edit: { op: "breakfast", day: Number(t.dataset.day), skip: true } }, "edit"); break;
      case "bf-restore": if (canEdit()) send({ question: `${t.dataset.day}일차 아침 식사 다시 넣기`, action: "edit", edit: { op: "breakfast", day: Number(t.dataset.day), skip: false } }, "edit"); break;
      case "ret-add": {
        if (!canEdit()) return;
        const it = (((state.plan || {}).return_stopovers || {}).items || []).find((x) => String(x.key || x.name) === t.dataset.key);
        if (it) send({ question: `돌아가는 길에 ${it.name} 들르기`, action: "edit", edit: { op: "return_stop", action: "add", stop: it } }, "edit");
        break;
      }
      case "ret-remove":
        if (canEdit()) send({ question: `돌아가는 길 ${t.dataset.name} 빼기`, action: "edit", edit: { op: "return_stop", action: "remove", stop: { key: t.dataset.key, name: t.dataset.name } } }, "edit");
        break;
      case "tg-more": { const blk = t.closest(".tg-block"); const on = !blk.classList.contains("show-all"); blk.classList.toggle("show-all", on); t.setAttribute("aria-expanded", String(on)); t.textContent = on ? "접기" : `더 보기 (${$$(".x-more", blk).length})`; break; }
      case "mode": setMode(t.dataset.v); break;
      case "undo": doUndo(); break;
      case "reconfirm-off": { try { sessionStorage.setItem("momo.reconfirm.off." + state.sessionId, "1"); } catch (err) { state.reconfirmOff = true; } renderBrochure({}); renderSide(); break; }
      case "reconfirm":
        if (DEMO) { toast("데모 모드에서는 바뀌지 않아요"); return; }
        state.reconfirming = true;
        send({ question: "", action: "approve" }, "approve", VIEW ? "" : "수정한 계획으로 다시 확정할게요");
        break;
      case "near-add": {
        if (!canEdit()) return;
        if (DEMO) { toast("데모 모드에서는 바뀌지 않아요"); return; }
        toast(`‘${t.dataset.name}’을 ${t.dataset.day}일차 ‘${t.dataset.afterName}’ 바로 다음에 넣는 중…`, 60000);
        state.focusAfter = t.dataset.pid;
        send({ question: `${t.dataset.afterName} 다음에 ${t.dataset.name} 추가`, action: "edit", edit: { op: "add", place_id: t.dataset.pid, after: t.dataset.after } }, "edit");
        break;
      }
      case "re-open": openRouteEditor(t.dataset.day ? Number(t.dataset.day) : null, t.dataset.focus || null); break;
      case "re-close": case "re-cancel": closeRouteEditor(); break;
      case "re-maptoggle": {
        const box = $("#re-mapbox"), open = box.hidden;
        box.hidden = !open; t.setAttribute("aria-expanded", String(open));
        if (open) setTimeout(() => { if (RE.map) { RE.map.invalidateSize(); drawReMap(true); } }, 40);
        break;
      }
      case "re-undo": (async () => {
        if (reDirtyDays().length) toast("저장하지 않은 순서는 지우고 되돌려요");
        await doUndo();
        if (RE.open) { reLoadFromPlan(); renderRouteEditor(); previewRoute(); drawReMap(true); loadOptimize(); }
      })(); break;
      case "re-save": saveRouteEditor(); break;
      case "re-opt-day": applyOptimize(Number(t.dataset.day)); break;
      case "re-opt-all": applyOptimize(null); break;
      case "re-opt-scope-all": applyOptimize(null, "all"); break;
      case "re-scope": { RE.scope = t.dataset.v; const box = $("#re-panel .re-opt"); if (box) box.innerHTML = optimizeHtml(); const f = $(`#re-panel [data-k="scope-${RE.scope}"]`); if (f) f.focus(); loadOptimize(); break; }
      case "tl-moveday": tlMoveToDay(t.dataset.pid, Number(t.dataset.day)); break;
      case "re-reset": { RE.days = JSON.parse(JSON.stringify(RE.orig)); RE.dirty = {}; renderRouteEditor(); previewRoute(); break; }
      case "map-layer-menu": { const c = $("#map-panel .map-ctrl"); c.dataset.open = c.dataset.open === "1" ? "" : "1"; renderMapCtrl(); const f = $("#mc-menu [aria-checked=true]"); if (f) f.focus(); break; }
      case "map-layer": { const c = $("#map-panel .map-ctrl"); c.dataset.open = ""; MAPL.failed[t.dataset.id] = false; setBaseLayer(t.dataset.id); const b = $("#map-panel .mc-layer .mc-btn"); if (b) b.focus(); break; }
      case "map-full": toggleMapFull(); break;
      case "swap": openSwap(t.dataset.pid, t.dataset.name, t.dataset.kind); break;
      case "swap-close": closeSwap(); break;
      case "swap-sort": SW.sort = t.dataset.v; renderSwap(); break;
      case "swap-open": SW.openOnly = !SW.openOnly; renderSwap(); break;
      case "swap-parts": { const k = t.dataset.i; SW.expanded[k] = !SW.expanded[k]; renderSwap(); const b = $(`#swap-dlg [data-act="swap-parts"][data-i="${cssEsc(k)}"]`); if (b) b.focus(); break; }
      case "swap-pick": chooseSwap(Number(t.dataset.i)); break;
      case "swap-search": { closeSwap(); const sec = $(".add-name"); if (sec) { sec.scrollIntoView({ behavior: scrollBehavior(), block: "start" }); const inp = $("[data-psearch]", sec); if (inp) inp.focus(); } break; }
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
    } else if (kind === "title") {
      const v = String($("#title-in").value || "").trim().slice(0, 40);
      state.titleEdit = false;
      if (!v) { renderBrochure({}); return; }
      sendTitle(v);
    } else if (kind === "trip-title") {
      const v = String(f.querySelector("input").value || "").trim().slice(0, 40);
      saveTripTitle(f.dataset.sid, v);
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
    const setH = () => document.documentElement.style.setProperty("--composer-h", form.offsetHeight + "px");
    setH(); window.addEventListener("resize", setH);
    if (window.ResizeObserver) new ResizeObserver(setH).observe(form);
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
    if (payload.needs_reconfirm != null) state.needsReconfirm = !!payload.needs_reconfirm;
    if (payload.undo_count != null) state.undoCount = Number(payload.undo_count) || 0;
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
    // 이 브라우저가 계획 주인이면(내 여행 계획 목록에 있으면) /view 에서 바로 고칠 수 있다
    if (VIEW && !DEMO_VIEW && payload.session_id && Trips.list().indexOf(payload.session_id) >= 0 && !PRINT) {
      state.owner = true;
      loadMode();
      fetch("/trips/" + encodeURIComponent(payload.session_id)).then((r) => (r.ok ? r.json() : null)).then((j) => {
        if (!j) return;
        if (j.draft_version) state.draftVersion = j.draft_version;
        if ("needs_reconfirm" in j) state.needsReconfirm = !!j.needs_reconfirm;
        if (j.undo_count != null) state.undoCount = Number(j.undo_count) || 0;
        if (j.plan_id) state.view.plan_id = j.plan_id;
        if (j.status === "확정") state.planStatus = "확정";
        renderBrochure({}); renderSide();
      }).catch(() => {});
      renderBrochure({}); renderSide();
    }
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
    document.addEventListener("pointerdown", tlPointerDown);
    document.addEventListener("keydown", tlKeydown);
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (e.target && e.target.id === "title-in") { e.preventDefault(); state.titleEdit = false; renderBrochure({}); const b = $(".title-pen"); if (b) b.focus(); return; }
      if (TOASTS.length && !document.querySelector("dialog[open]") && !TP.trigger && !$("#map-panel.map-full") && !TL && !(e.target.closest && e.target.closest(".mc-layer"))) {
        e.preventDefault(); closeToast(TOASTS[TOASTS.length - 1]); return;
      }
      const mc = $("#map-panel .map-ctrl");
      if (mc && mc.dataset.open === "1") { mc.dataset.open = ""; renderMapCtrl(); const b = $("#map-panel .mc-layer .mc-btn"); if (b) b.focus(); e.preventDefault(); return; }
      if ($("#map-panel.map-full") && !(TP.trigger)) { e.preventDefault(); toggleMapFull(false); }
    });
    loadLayers();
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
      if (t.id === "hist-q") renderHistory();
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
      fetchMessages("demo").then((rows) => {
        if (rows && rows.length) restoreThread(rows);
        else bubble("me", "순천/구례 · 12월 12일(토) ~ 12월 13일(일) · 1박 2일 · 부모님 동반 4명 · KTX·SRT + 렌터카");
        const th = $("#thread"), br = $("#brochure"); if (th && br) th.appendChild(br);
        return demoPlanResponse(params.get("status") === "확정");
      }).then((j) => { handleResponse(Object.assign({}, j, { plan_no: 3 }), rowsExist() ? "restore" : "chat"); foldThread(); })
        .catch(() => toast("데모 계획서(sample_plan.json)를 불러오지 못했어요."));
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
