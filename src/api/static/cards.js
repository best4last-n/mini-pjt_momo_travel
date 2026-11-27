// 상세 일정표(카드형, 1열) — 대화 화면과 공유 페이지가 함께 씀
const WD = "일월화수목금토";
const LICENSE = { Type1: "공공누리 제1유형", Type3: "공공누리 제3유형(변경 금지)" };
const TYPE_LABEL = { place: "관광", meal: "식사", stay: "숙박", move: "이동" };
const MOMO_SVG = `<svg viewBox="0 0 120 80" aria-label="모모 일러스트"><rect width="120" height="80" fill="var(--sky-soft)"/>
<ellipse cx="60" cy="46" rx="24" ry="18" fill="var(--sky)"/><circle cx="74" cy="38" r="11" fill="var(--sky)"/>
<circle cx="77" cy="36" r="2" fill="#223"/><path d="M85 39 l8 2 l-8 3z" fill="#f4a63a"/>
<rect x="64" y="22" width="20" height="6" rx="2" fill="#3a5a8c"/><rect x="68" y="17" width="12" height="7" rx="2" fill="#3a5a8c"/></svg>`;

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

function renderItinerary(el, items) {
  el.innerHTML = "";
  if (!items || !items.length) return;
  const dates = [...new Set(items.map(i => i.date))];
  for (const d of dates) {
    const dt = new Date(d + "T00:00:00");
    const sec = document.createElement("section");
    sec.className = "day";
    const dayItems = items.filter(i => i.date === d);
    const moveSum = dayItems.filter(i => i.type !== "move").reduce((a, i) => a + (i.travel_minutes_from_prev || 0), 0);
    sec.innerHTML = `<h3>${dates.indexOf(d) + 1}일차 · ${dt.getMonth() + 1}월 ${dt.getDate()}일(${WD[dt.getDay()]})
      <small>현지 이동 합계 약 ${moveSum}분</small></h3>`;
    for (const it of dayItems) {
      if (it.travel_minutes_from_prev) {
        const mv = document.createElement("div");
        mv.className = "leg";
        mv.textContent = `▶ 약 ${it.travel_minutes_from_prev}분 · 자차(추정)`;
        sec.appendChild(mv);
      }
      const card = document.createElement("article");
      card.className = "card t-" + it.type;
      if (it.type === "move") {
        card.innerHTML = `<div class="body"><span class="time">${esc(it.start_time)}~${esc(it.end_time)}</span>
          <b>${esc(it.place_name)}</b> <span class="tag">이동 · 직선거리 추정</span></div>`;
      } else {
        const img = it.image_url
          ? `<figure><img src="${esc(it.image_url)}" alt="${esc(it.place_name)}" loading="lazy"><figcaption>사진: 한국관광공사 · ${esc(LICENSE[it.image_license] || it.image_license || "공공누리")}</figcaption></figure>`
          : `<figure class="momo">${MOMO_SVG}<figcaption>이미지 없음 · 모모 일러스트</figcaption></figure>`;
        const badge = it.hours_status === "확인 필요"
          ? `<span class="badge warn">운영시간 확인 필요</span>`
          : (it.hours_text ? `<span class="badge ok">운영 ${esc(it.hours_text)}</span>` : "");
        card.innerHTML = `${img}<div class="body">
          <div><span class="time">${esc(it.start_time)}~${esc(it.end_time)}</span> <span class="tag">${TYPE_LABEL[it.type] || ""}</span> ${badge}</div>
          <h4>${esc(it.place_name)}</h4>
          <p class="reason">${esc(it.reason)}</p>
          ${it.summary ? `<p class="summary">${esc(it.summary)}</p>` : ""}
          ${(it.notes || []).filter(n => n !== "출발" && n !== "귀가").map(n => `<p class="note">※ ${esc(n)}</p>`).join("")}
          <p class="src">출처: ${esc(it.source)}</p></div>`;
      }
      sec.appendChild(card);
    }
    el.appendChild(sec);
  }
}
