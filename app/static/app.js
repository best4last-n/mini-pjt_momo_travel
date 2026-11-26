/* 모모의 여행 상담소 — 화면 로직 (빌드 없음, 프레임워크 없음)
 * 구성: 상수·유틸 → 상태 → API → 대화 → 여행 조건 → 브로셔 → 경비 → 정산(공유 페이지) → 지도 → 배치(PC/모바일) → 음성 → 시작
 * 외부 데이터(관광공사·LLM)에서 온 글자는 모두 esc()를 거쳐 넣는다.
 */
(function () {
  "use strict";

  /* ───────── 상수·유틸 ───────── */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BOOT = window.MOMO_BOOT || {};
  const params = new URLSearchParams(location.search);
  const READ_ONLY = !!BOOT.readOnly;
  const DEMO = !READ_ONLY && params.get("demo") === "1";
  const PRINT = READ_ONLY && params.get("print") === "1";
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
  const MQ_PC = window.matchMedia("(min-width: 1024px)");
  const DAY_COLORS = ["#3a5a8c", "#d0703a", "#2f8a5b", "#8a4fa3", "#a8781c"];
  const WD = "일월화수목금토";
  const LICENSE = { Type1: "공공누리 제1유형", Type3: "공공누리 제3유형(변경 금지)" };
  const STATUS = {
    confirmed: { t: "확정", c: "ok" }, assumed: { t: "가정", c: "warn" },
    missing: { t: "미정", c: "bad" }, optional: { t: "선택", c: "off" },
  };
  const TYPE_LABEL = { place: "관광", meal: "식사", stay: "숙박", move: "이동" };
  const COST_CATS = ["장거리 교통", "현지 교통", "유류비", "통행료", "렌터카", "식비", "입장료", "숙박", "기타"];
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
 <path d="M11 41 L1 33 L3 43 L0 51 L13 47 Z" fill="var(--bird-wing)"/>
 <ellipse cx="28" cy="41" rx="20" ry="16" fill="var(--bird)"/>
 <circle cx="41" cy="28" r="12" fill="var(--bird)"/>
 <ellipse cx="35" cy="46" rx="12" ry="8" fill="#fff"/>
 <path d="M13 39 C17 30 30 32 33 41 C29 47 18 48 13 39Z" fill="var(--bird-wing)"/>
 <g transform="rotate(-10 26 47)"><rect x="19" y="42" width="14" height="9.5" rx="1" fill="#fffaf0" stroke="#c9b48a" stroke-width=".9"/>
 <path d="M19.4 42.4 L26 47.3 L32.6 42.4" fill="none" stroke="#c9b48a" stroke-width=".9"/><circle cx="26" cy="47.4" r="1.5" fill="#d0603a"/></g>
 <circle cx="45" cy="28.5" r="2.5" fill="#1f2d3d"/><circle cx="45.9" cy="27.6" r=".85" fill="#fff"/>
 <circle cx="47.5" cy="33" r="2.4" fill="#f4a3b4" opacity=".75"/>
 <path d="M52 27.5 L60 30 L52 32.6Z" fill="#f4a63a"/>
 <path d="M29 23.5 C29.5 11 51 10 53 22.5 L53 24.5 L29 24.5Z" fill="#2c4a7a"/>
 <path d="M44 23 L59 24.6 Q58 27.4 46 26.6Z" fill="#1d3457"/>
 <circle cx="40" cy="17.5" r="2.4" fill="#d0603a"/>
 <path d="M25 56 v4 M33 56 v4" stroke="#f4a63a" stroke-width="2" stroke-linecap="round"/>
</symbol>
<symbol id="ico-fork" viewBox="0 0 16 16"><path d="M4.5 1.5v4.2M3 1.5v3.6a1.5 1.5 0 0 0 3 0V1.5M4.5 6.6v7.9M12 1.5c-1.6 1-2.2 3-2.2 5.3h2.2v7.7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-chev" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="ico-send" viewBox="0 0 20 20"><path d="M2.5 9.6 17 3 12.6 17.2l-2.7-5.6zM9.9 11.6 17 3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></symbol>
<symbol id="ico-pin" viewBox="0 0 16 16"><path d="M8 15s5-4.6 5-8.4A5 5 0 0 0 3 6.6C3 10.4 8 15 8 15z" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="8" cy="6.6" r="1.8" fill="currentColor"/></symbol>
</defs></svg>`;
  const bird = (cls = "bird") => `<svg class="${cls}" aria-hidden="true" focusable="false"><use href="#momo-bird"/></svg>`;
  const ico = (id, cls = "ico") => `<svg class="${cls}" aria-hidden="true" focusable="false"><use href="#${id}"/></svg>`;
  const MOMO_ILLUST = `<svg class="illust" viewBox="0 0 160 110" role="img" aria-label="사진이 없어 모모 그림으로 대신했어요">
<rect width="160" height="110" fill="var(--season-soft)"/>
<ellipse cx="34" cy="28" rx="18" ry="6" fill="var(--surface)" opacity=".9"/><ellipse cx="128" cy="20" rx="14" ry="5" fill="var(--surface)" opacity=".9"/>
<path d="M8 92 C40 70 70 96 100 74 S150 60 156 44" fill="none" stroke="var(--season-deep)" stroke-width="1.4" stroke-dasharray="3 4" opacity=".6"/>
<use href="#momo-bird" x="54" y="22" width="60" height="60"/></svg>`;

  /* 안내 데스크의 모모 — 부위별 그룹(m-*)을 CSS가 상태(data-mood)에 따라 움직인다 */
  const DESK_SVG = `<svg class="desk-svg" viewBox="0 0 150 112" role="img" aria-label="안내 데스크에 앉은 우체부 철새 모모">
<circle cx="86" cy="54" r="38" fill="var(--season-soft)"/>
<g class="m-spark" fill="var(--season-deep)"><path d="M40 30 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2z"/><path d="M132 60 l1.5 4 4 1.5 -4 1.5 -1.5 4 -1.5 -4 -4 -1.5 4 -1.5z"/><path d="M58 14 l1.2 3 3 1.2 -3 1.2 -1.2 3 -1.2 -3 -3 -1.2 3 -1.2z"/></g>
<g transform="translate(46 20)"><g class="m-jump"><g class="m-breathe">
 <path d="M11 41 L1 33 L3 43 L0 51 L13 47 Z" fill="var(--bird-wing)"/>
 <g class="m-wing-back"><path d="M30 34 C36 20 50 18 56 22 C50 30 42 38 32 42Z" fill="var(--bird-wing)"/></g>
 <ellipse cx="28" cy="41" rx="20" ry="16" fill="var(--bird)"/>
 <ellipse cx="35" cy="46" rx="12" ry="8" fill="#fff"/>
 <g class="m-head">
  <circle cx="41" cy="28" r="12" fill="var(--bird)"/>
  <g class="m-eye"><circle cx="45" cy="28.5" r="2.5" fill="#1f2d3d"/><circle cx="45.9" cy="27.6" r=".85" fill="#fff"/></g>
  <path class="m-eye-closed" d="M42.4 29 q2.6 2.2 5.2 0" fill="none" stroke="#1f2d3d" stroke-width="1.5" stroke-linecap="round"/>
  <circle cx="47.5" cy="33" r="2.4" fill="#f4a3b4" opacity=".75"/>
  <path d="M52 27.5 L60 30 L52 32.6Z" fill="#f4a63a"/>
  <path d="M29 23.5 C29.5 11 51 10 53 22.5 L53 24.5 L29 24.5Z" fill="#2c4a7a"/>
  <path d="M44 23 L59 24.6 Q58 27.4 46 26.6Z" fill="#1d3457"/>
  <circle cx="40" cy="17.5" r="2.4" fill="#d0603a"/>
 </g>
 <g class="m-wing"><path d="M13 39 C17 30 30 32 33 41 C29 47 18 48 13 39Z" fill="var(--bird-wing)"/></g>
</g></g></g>
<g class="m-letters"><rect x="15" y="63" width="30" height="8" rx="1" fill="#fffaf0" stroke="#c9b48a" stroke-width=".8" transform="rotate(-4 30 67)"/>
 <rect x="17" y="58" width="28" height="9" rx="1" fill="#fffdf6" stroke="#c9b48a" stroke-width=".8"/><path d="M17.4 58.4 L31 63.4 L44.6 58.4" fill="none" stroke="#c9b48a" stroke-width=".8"/>
 <g class="m-mark"><circle cx="31" cy="62.5" r="5.2" fill="none" stroke="#d0603a" stroke-width="1.4"/><text x="31" y="64.6" text-anchor="middle" font-size="5" fill="#d0603a" font-weight="700">확정</text></g></g>
<g class="m-stamp"><rect x="25" y="34" width="12" height="4" rx="1" fill="#d0603a"/><rect x="29" y="22" width="4" height="12" rx="1.5" fill="#8a5a3a"/><circle cx="31" cy="21" r="3.4" fill="#8a5a3a"/></g>
<rect x="6" y="70" width="138" height="7" rx="2" fill="var(--desk-top)"/>
<rect x="12" y="77" width="126" height="33" rx="2" fill="var(--desk)"/>
<rect x="52" y="84" width="46" height="15" rx="2" fill="var(--surface)" stroke="var(--brand)" stroke-width="1"/>
<text x="75" y="94.6" text-anchor="middle" font-size="8.5" fill="var(--brand)" font-family="Jua, 'Noto Sans KR', sans-serif">여행 상담</text>
<g class="m-bub m-bub-think"><circle cx="124" cy="26" r="13" fill="var(--surface)" stroke="var(--line-2)"/><circle cx="111" cy="41" r="3" fill="var(--surface)" stroke="var(--line-2)"/>
 <circle class="bd" cx="118" cy="26" r="1.8" fill="var(--ink-2)"/><circle class="bd" cx="124" cy="26" r="1.8" fill="var(--ink-2)"/><circle class="bd" cx="130" cy="26" r="1.8" fill="var(--ink-2)"/></g>
<g class="m-bub m-bub-ask"><circle cx="124" cy="26" r="13" fill="var(--surface)" stroke="var(--line-2)"/><path d="M113 36 l-4 7 9 -4z" fill="var(--surface)" stroke="var(--line-2)"/>
 <text x="124" y="31.5" text-anchor="middle" font-size="15" font-weight="700" fill="var(--brand)" font-family="Jua, sans-serif">?</text></g>
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
    ["#cond-panel", "#brochure", "#act-panel"].forEach((s) => {
      const el = $(s);
      if (!el) return;
      el.setAttribute("aria-busy", on ? "true" : "false");
      el.classList.toggle("is-loading", on && kind !== "chat");
    });
    const sendBtn = $("#send");
    if (sendBtn) sendBtn.disabled = on;
  }

  /** kind: chat | edit | set | approve */
  async function send(body, kind, userText) {
    if (state.busy) { toast("모모가 아직 날아가는 중이에요. 잠시만 기다려 주세요."); return; }
    if (DEMO && kind !== "chat") { toast("데모 모드에서는 바뀌지 않아요"); return; }
    if (userText) bubble("me", userText);
    const wait = (kind === "chat" || kind === "approve") ? loadingBubble() : null;
    if (kind === "edit" || kind === "set") toast("모모가 계획서를 고치는 중이에요…", 60000);
    setBusy(true, kind);
    setMood("thinking");
    let j;
    if (DEMO) {
      await new Promise((r) => setTimeout(r, 700));
      j = { answer: "데모 모드에서는 모모가 실제로 날아가지 않아요. 브로셔와 지도, 버튼 모양을 둘러봐 주세요!", status: "정보", stage: state.stage, quick_replies: ["알아서 짜 줘"] };
    } else {
      j = await callApi(body);
    }
    if (wait) wait.stop();
    setBusy(false, kind);
    if (kind === "edit" || kind === "set") $("#toast") && $("#toast").classList.remove("show");
    handleResponse(j, kind);
  }

  function handleResponse(j, kind) {
    if (j.session_id) state.sessionId = j.session_id;
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
    const b = bubble("momo", j.answer || "", { replies: j.quick_replies || j.chips || [], changes, status: j.status, longPlan: !!j.plan && (j.stage === "plan" || j.stage === "refine") });
    if (changes.length) toast(changes, 5200);
    if (j.plan) {
      state.plan = j.plan;
      if (j.plan.trip && j.plan.trip.checklist) state.conditions = j.plan.trip.checklist;
      if (j.plan.trip && j.plan.trip.start_date) document.documentElement.dataset.season = seasonOf(j.plan.trip.start_date);
      const inPlace = kind === "edit" || kind === "set";
      renderBrochure({ moveAfter: inPlace ? null : b });
      if (!inPlace && b) b.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    } else if (state.plan) {
      renderBrochure({}); // 상태(확정 등)만 바뀐 경우 편집 버튼 갱신
    }
    if (Array.isArray(j.conditions) && j.conditions.length) state.conditions = j.conditions;
    if (j.progress) state.progress = j.progress;
    else if (j.plan && j.plan.trip && j.plan.trip.progress) state.progress = j.plan.trip.progress;
    else if (j.plan || j.conditions) state.progress = null;
    setMood(moodFor(j.status));
    renderSide();
    if (!j.plan && b && kind !== "edit" && kind !== "set") b.scrollIntoView({ behavior: scrollBehavior(), block: "nearest" });
  }

  /* ───────── 대화 ───────── */
  function splitAnswer(text) {
    const lines = String(text || "").split(/\r?\n/);
    const idx = lines.findIndex((l) => /^\s*■/.test(l));
    if (idx < 0) return { head: lines.join("\n").trim(), tail: "" };
    return { head: lines.slice(0, idx).join("\n").trim(), tail: lines.slice(idx).join("\n").trim() };
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
      wrap.innerHTML = `<div class="avatar">${bird()}</div><div class="bub-col"><div class="bub">
        <span class="who">모모${opt.status && opt.status !== "오류" ? ` · <em>${esc(opt.status)}</em>` : ""}</span>
        ${changes}${head ? `<p class="txt">${richText(head)}</p>` : ""}${more}</div>${replies}</div>`;
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

  function welcome() {
    const thread = $("#thread");
    if (!thread) return;
    const ex = ["12월 12일에 부모님 모시고 순천 1박 2일, 서울에서 KTX로 가요", "아이 둘이랑 경주 당일치기, 대구에서 차로 출발", "수원 화성 쪽으로 여유롭게 하루"];
    thread.insertAdjacentHTML("beforeend", `<section class="hello" aria-labelledby="hello-h">
      <div class="hello-art">${bird("bird big")}</div>
      <div><p class="eyebrow">우체부 철새 모모의 여행 상담소</p>
      <h2 id="hello-h" class="display">어디로 여행 편지를 부칠까요?</h2>
      <p>가고 싶은 지역, 날짜, 함께 가는 분을 알려 주세요. 운영시간·휴무일까지 확인한 일정을 엽서처럼 배달해 드릴게요.
      지금은 <b>경주 · 순천/구례 · 수원</b>을 꼼꼼히 다뤄요.</p>
      <div class="replies static" role="group" aria-label="이렇게 말해 보세요">${ex.map((r) => `<button type="button" class="chip" data-act="qr" data-q="${esc(r)}">${esc(r)}</button>`).join("")}</div></div>
    </section>`);
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
    const rows = conds.map((c) => {
      const st = STATUS[c.status] || STATUS.missing;
      const val = c.value ? esc(c.value) : `<span class="ph">${c.status === "optional" ? "없음" : "아직 몰라요"}</span>`;
      const inner = `<i class="dot ${st.c}" aria-hidden="true"></i><span class="lbl">${esc(c.label)}</span><span class="val">${val}</span><span class="st ${st.c}">${st.t}</span>`;
      if (!editable) return `<li class="cond"><div class="cond-row ro">${inner}</div></li>`;
      const open = state.openCond === c.key;
      return `<li class="cond${open ? " open" : ""}"><button type="button" class="cond-row" data-act="cond" data-key="${esc(c.key)}" aria-expanded="${open}" aria-controls="ed-${esc(c.key)}"
        aria-label="${esc(c.label)}: ${esc(c.value || "미정")} (${st.t}) — 눌러서 바꾸기">${inner}${ico("ico-chev", "ico chev")}</button>
        ${open ? condEditor(c) : ""}</li>`;
    }).join("");
    if (!$("#cond-body", panel)) {
      panel.innerHTML = `<div class="panel-h"><h2 class="ph-title">여행 조건</h2><p class="sum" id="cond-sum" aria-label="조건 요약"></p></div>
        ${progressHtml("prog")}<div id="cond-body"></div><div class="bar-loading" aria-hidden="true"></div>`;
    }
    $("#cond-sum", panel).innerHTML = summaryHtml(counts);
    $("#cond-body", panel).innerHTML = `<ul class="cond-list">${rows}</ul>`;
    const ms = $("#m-cond-sum");
    if (ms) ms.innerHTML = summaryHtml(counts);
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
    if (c.input === "date") {
      body = `<form class="ed-form" data-form="set" data-key="${esc(c.key)}"><label class="sr" for="in-${id}">${esc(c.label)}</label>
        <input id="in-${id}" type="date" name="v" value="${esc(c.iso || "")}" required><button class="btn sm primary">바꾸기</button></form>`;
    } else if (c.input === "text") {
      body = `<form class="ed-form" data-form="set" data-key="${esc(c.key)}"><label class="sr" for="in-${id}">${esc(c.label)}</label>
        <input id="in-${id}" type="text" name="v" value="${esc(c.status === "confirmed" ? c.value || "" : "")}" placeholder="${esc(c.value || "직접 적어 주세요")}" autocomplete="off" maxlength="60" required>
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

  function sendSet(key, value) {
    const c = currentConds().find((x) => x.key === key);
    state.openCond = null;
    renderConditions();
    send({ question: `[조건 변경] ${c ? c.label : key} → ${value}`, action: "set", set: { key, value: String(value) } }, "set");
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
    days.forEach((d) => {
      html.push(daySection(d, P, plan));
      if (d.day === lastDay && ld.inbound) html.push(ticket(ld.inbound, "오는 길", d.date));
    });
    if (canEdit() && days.length) html.push(addByName(days));
    if ((plan.warnings || []).length) {
      html.push(`<section class="warn-box" aria-labelledby="w-h"><h3 id="w-h">모모가 미리 챙긴 주의할 점</h3><ul>${plan.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></section>`);
    }
    if (plan.costs) html.push(costTable(plan.costs));
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
    const statusTxt = READ_ONLY ? "확정된 여행 계획서" : state.planStatus === "확정" ? "확정된 여행 계획서" : "여행 계획서 초안 · 확정 전";
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

  function addByName(days) {
    return `<form class="add-name" data-form="add-name" aria-labelledby="an-h">
      <h3 id="an-h">가고 싶은 곳이 더 있나요?</h3>
      <p class="muted">장소 이름을 적으면 모모가 찾아서 동선에 맞게 넣어 드려요.</p>
      <div class="row"><label class="sr" for="an-in">장소 이름</label><input id="an-in" name="name" type="text" placeholder="예) 낙안읍성, 사성암" maxlength="40" autocomplete="off" required>
      <label class="sr" for="an-day">넣을 날</label>${daySelect(days, "an-day", true)}<button class="btn sm primary">추가</button></div></form>`;
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
  function costRows(costs) {
    return (costs.items || []).map((it) => `<tr><th scope="row"><span class="cat">${esc(it.category)}</span>${esc(it.label)}${it.basis ? `<small>${esc(it.basis)}</small>` : ""}</th>
      <td class="num-c">${it.unknown ? `<span class="badge warn">확인 필요</span>` : won(it.amount) + (it.estimated ? `<small>추정</small>` : "")}</td></tr>`).join("");
  }
  function costTable(costs) {
    return `<section class="costs-b" aria-labelledby="cost-h"><h3 id="cost-h">예상 경비 <span class="muted">${esc(costs.people || "")}${costs.people ? "명 기준" : ""}</span></h3>
      <table class="tbl"><tbody>${costRows(costs)}</tbody>
      <tfoot><tr><th scope="row">합계</th><td class="num-c">${won(costs.total)}</td></tr>
      ${costs.per_person != null ? `<tr><th scope="row">1인당</th><td class="num-c">${won(costs.per_person)}</td></tr>` : ""}
      ${costs.budget ? `<tr class="${costs.over_budget ? "over" : ""}"><th scope="row">최대 예산</th><td class="num-c">${won(costs.budget)}${costs.over_budget ? ` <small>예산 초과</small>` : ""}</td></tr>` : ""}</tfoot></table>
      ${costs.note ? `<p class="muted small">${esc(costs.note)}</p>` : ""}</section>`;
  }
  function renderCosts() {
    const el = $("#cost-panel");
    if (!el) return;
    const c = state.plan && state.plan.costs;
    if (!c) { el.hidden = true; el.innerHTML = ""; return; }
    el.hidden = false;
    let bar = "";
    if (c.budget) {
      const pct = Math.round((c.total / c.budget) * 100);
      const over = c.over_budget || c.total > c.budget;
      bar = `<div class="budget${over ? " over" : ""}"><div class="b-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.min(pct, 100)}" aria-label="예산 대비 ${pct}%"><span style="width:${Math.min(pct, 100)}%"></span></div>
        <p>${over ? `예산을 <b>${won(c.total - c.budget)}</b> 넘어요` : `예산 ${won(c.budget)}의 <b>${pct}%</b>`}</p></div>`;
    }
    const wasOpen = el.querySelector("details[open]") != null;
    el.innerHTML = `<div class="panel-h"><h2 class="ph-title">예상 경비</h2><p class="sum">${esc(c.people || "")}${c.people ? "명 · " : ""}추정</p></div>
      <p class="cost-total"><b>${won(c.total)}</b><span>1인당 ${won(c.per_person)}</span></p>${bar}
      <details${wasOpen ? " open" : ""}><summary>분류별 금액</summary><ul class="cost-list">${(c.items || []).map((it) => `<li><span>${esc(it.category)}<small>${esc(it.label)}</small></span><b>${it.unknown ? "확인 필요" : won(it.amount)}</b></li>`).join("")}</ul></details>`;
  }

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
    if (!host || !READ_ONLY || !state.plan) return;
    const E = state.expenses;
    const days = state.plan.itinerary || [];
    const cats = Array.from(new Set(COST_CATS.concat(((state.plan.costs || {}).items || []).map((i) => i.category))));
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
      <div class="f"><label for="ex-cat">분류</label><select id="ex-cat" name="category" class="sel">${cats.map((c) => `<option>${esc(c)}</option>`).join("")}</select></div>
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
    const totals = `<dl class="totals">
      ${local.distance_km != null ? `<div><dt>현지 이동</dt><dd>${esc(km(local.distance_km))} · 약 ${fmtMin(local.travel_min)}</dd></div>` : ""}
      ${ldMin ? `<div><dt>장거리 왕복</dt><dd>${esc(ldMode || "")} 약 ${fmtMin(ldMin)}</dd></div>` : ""}
      <div class="est"><dt class="sr">참고</dt><dd>모든 이동시간은 직선거리 기반 추정이에요</dd></div></dl>`;
    let act = "";
    if (READ_ONLY) {
      const id = esc(BOOT.planId);
      act = exportLinks({ html_url: `/plan/${id}.html`, docx_url: `/plan/${id}.docx`, print_url: `/plan/${id}?print=1`, ics_url: `/plan/${id}.ics`, report_url: `/plan/${id}/report` });
    } else if (state.planStatus === "확정" && state.exports) {
      act = `<p class="state ok"><i class="dot ok"></i>확정됐어요. 원하는 형식으로 가져가세요.</p>${exportLinks(state.exports)}`;
    } else if (state.planStatus === "승인 대기") {
      const pg = getProgress();
      const full = Number(pg.percent) >= 100;
      const na = (pg.assumed || []).length, nm = (pg.missing || []).length;
      const left = [na ? `가정 ${na}개` : "", nm ? `미정 ${nm}개` : ""].filter(Boolean).join("·") || "확정하지 않은 조건";
      act = `${full ? "" : `<p class="approve-note">${esc(left)}가 남아 있어요. 그대로 확정해도 되고, ${MQ_PC.matches ? "위 '여행 조건'에서" : "맨 위 '여행 조건'에서"} 눌러 바꿀 수도 있어요.</p>`}
        <button type="button" class="btn primary wide" data-act="approve"${state.busy ? " disabled" : ""}>${full ? "이 계획으로 확정할게요" : "지금 조건 그대로 확정할게요"}</button>
        <p class="muted small">확정하면 공유 링크 · HTML · Word · PDF · 캘린더 파일을 만들어 드려요. 확정 전에는 어디에도 저장·공유하지 않아요.</p>`;
    }
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
    if (state.mapDay !== "all" && !M.groups[state.mapDay]) state.mapDay = "all";
    if (tabs) {
      tabs.innerHTML = [["all", "전체"]].concat(plan.itinerary.map((d) => [String(d.day), `${d.day}일차`])).map(([v, t]) =>
        `<button type="button" role="tab" class="tab${String(state.mapDay) === v ? " on" : ""}" aria-selected="${String(state.mapDay) === v}" data-act="mapday" data-v="${v}"${v !== "all" ? ` style="--c:${dayColor(v)}"` : ""}>${v !== "all" ? `<i class="dot" style="background:${dayColor(v)}"></i>` : ""}${t}</button>`).join("");
    }
    applyMapDay();
  }
  function applyMapDay() {
    if (!M.map) return;
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
    ["#cond-panel", "#cost-panel", "#map-panel", "#act-panel"].forEach((s) => { const x = $(s); if (x && x.parentNode !== side) side.appendChild(x); });
  }
  function placePanels() {
    const pc = MQ_PC.matches;
    const side = $("#side");
    const cond = $("#cond-panel"), mapP = $("#map-panel"), cost = $("#cost-panel"), act = $("#act-panel");
    if (!side) return;
    if (pc) {
      stashPanels();
    } else {
      const mc = $("#m-cond-slot"), mm = $("#m-map-body"), ma = $("#m-act-slot");
      if (cond && mc && cond.parentNode !== mc) mc.appendChild(cond);
      if (mapP && mm && mapP.parentNode !== mm) mm.appendChild(mapP);
      if (ma) { if (cost && cost.parentNode !== ma) ma.appendChild(cost); if (act && act.parentNode !== ma) ma.appendChild(act); }
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
    const hello = $(".hello"); if (hello) hello.remove();
    bubble("momo", auto ? "지난번에 보던 계획을 다시 펼쳐 놨어요." : "저장된 계획을 불러왔어요.");
    const st = j.status === "초안" ? "승인 대기" : j.status;
    handleResponse(Object.assign({}, j, { status: st, quick_replies: [] }), "chat");
    if (j.progress) { state.progress = j.progress; updateProgress(); renderActions(); }
  }

  /* ───────── 이벤트(위임) ───────── */
  function onClick(e) {
    const t = e.target.closest("[data-act]");
    if (!t) return;
    const act = t.dataset.act;
    const plan = state.plan;
    switch (act) {
      case "qr": if (!READ_ONLY) send({ question: t.dataset.q }, "chat", t.dataset.q); break;
      case "cond": {
        const k = t.dataset.key;
        state.openCond = state.openCond === k ? null : k;
        renderConditions();
        const ed = document.getElementById("ed-" + k);
        if (ed) { const f = ed.querySelector("input, button.chip"); if (f) f.focus(); }
        else { const b = $(`.cond-row[data-key="${CSS.escape(k)}"]`); if (b) b.focus(); }
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
      case "mapday": state.mapDay = t.dataset.v === "all" ? "all" : Number(t.dataset.v); renderMap(); break;
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
      const v = f.querySelector("input").value.trim();
      if (v) sendSet(f.dataset.key, v);
    } else if (kind === "add-name") {
      if (!canEdit()) return;
      const name = f.querySelector("input").value.trim();
      if (!name) return;
      const day = f.querySelector("select").value;
      const edit = { op: "add", name };
      if (day) edit.day = Number(day);
      sendEdit(edit, `${name} 추가${day ? ` (${day}일차)` : ""}`);
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

  /* ───────── 시작 ───────── */
  function init() {
    document.body.insertAdjacentHTML("afterbegin", SPRITE);
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
    window.addEventListener("resize", () => { clearTimeout(init._r); init._r = setTimeout(refitMap, 150); });

    if (READ_ONLY) {
      const payload = BOOT.payload || {};
      state.plan = payload.plan || legacyToPlan(payload);
      state.planStatus = "확정";
      state.conditions = (state.plan.trip && state.plan.trip.checklist) || null;
      if (PRINT) document.documentElement.classList.add("print-mode");
      if (state.plan.trip && state.plan.trip.start_date) document.documentElement.dataset.season = seasonOf(state.plan.trip.start_date);
      renderBrochure({});
      renderSide();
      loadExpenses();
      if (PRINT) printWhenReady();
      return;
    }

    const desk = $("#desk");
    if (desk) { desk.innerHTML = DESK_SVG; setMood("idle"); }
    initComposer();
    initVoice();
    welcome();
    renderSide();
    placePanels();
    if (!DEMO) { const cur = Trips.current(); if (cur) loadTrip(cur, true); }
    if (DEMO) {
      document.documentElement.classList.add("demo");
      const q = "12월 12일 토요일에 부모님 모시고 서울에서 순천 1박 2일 가요. KTX 타고 가요";
      bubble("me", q);
      fetch("/static/sample_plan.json").then((r) => r.json()).then((plan) => {
        const done = params.get("status") === "확정";
        handleResponse({
          answer: "부모님과 함께하는 순천/구례 1박 2일 계획서를 만들었어요!\n※ 걷기 편한 평지 위주로 골랐고, 계단이 많은 곳은 주의 문구를 달았어요.\n※ 현지 이동은 렌터카로 가정했어요. 오른쪽 '여행 조건'에서 바로 바꿀 수 있어요.\n☁ 날씨: 12월 중순 순천은 낮 7~10℃ 안팎이에요(평년 기준).\n■ 1일차 12월 12일(토)\n12:00–13:00 남녘들밥상\n13:15–15:15 순천만국가정원\n15:40–17:10 순천만습지\n■ 2일차 12월 13일(일)\n10:50–12:20 화엄사\n12:55–13:55 사성암\n여유가 되면 낙안읍성도 들러 보세요.",
          status: done ? "확정" : "승인 대기", stage: "plan", session_id: "demo", draft_version: "demo-1", plan,
          quick_replies: ["2일차를 더 여유롭게", "낙안읍성도 넣어 줘", "이대로 좋아요"],
          share_url: "#", html_url: "#", docx_url: "#", print_url: "#", ics_url: "#", report_url: "#",
        }, "chat");
      }).catch(() => toast("데모 계획서(sample_plan.json)를 불러오지 못했어요."));
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
