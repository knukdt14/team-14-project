// 1페이지(여행지 추첨) 스크립트.
//  - 필터 칩/셀렉트를 바꾸면 바로 조건이 적용된다 (토글처럼 동작).
//  - 카드 열기 / 다시 뽑기는 페이지 새로고침 없이 그 카드만 갈아끼운다.
// JS 없이도 폼 제출만으로 전부 동작하도록 만들어져 있고, 여기서는 그 위에
// 편의만 얹는다.
(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wait = (ms) => new Promise((res) => setTimeout(res, ms));

  function showFlash(message, kind) {
    let stack = document.querySelector(".flash-float");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "flash-stack flash-float";
      document.body.appendChild(stack);
    }
    const el = document.createElement("div");
    el.className = "flash flash-" + (kind || "info");
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .4s ease";
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 420);
    }, 2600);
  }

  // ------------------------------------------------------------ 필터 토글
  // 칩을 누르면 "조건 적용"을 따로 누르지 않아도 바로 반영된다.
  const filterForm = document.getElementById("pick-filter-form");
  if (filterForm) {
    filterForm.addEventListener("change", (e) => {
      if (!e.target.matches("select, input[type=checkbox]")) return;
      filterForm.submit();
    });
  }

  // ------------------------------------------------- 카드 부분 갱신 (AJAX)
  const grid = document.getElementById("pick-grid");
  if (!grid) return;

  const FLIP_OUT_MS = 190;

  grid.addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-card-action]");
    if (!form) return;

    const slot = form.closest(".pick-slot");
    if (!slot || slot.dataset.busy === "1") return;

    e.preventDefault();
    slot.dataset.busy = "1";
    slot.classList.remove("is-flipped");
    if (!reduceMotion) slot.classList.add("is-flipping");

    try {
      const res = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "fetch" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();

      if (!reduceMotion) await wait(FLIP_OUT_MS);

      if (data.html) {
        slot.innerHTML = data.html;
        slot.classList.remove("is-flipping");
        if (!reduceMotion) {
          void slot.offsetWidth;               // 애니메이션 재시작용 리플로우
          slot.classList.add("is-flipped");
          setTimeout(() => slot.classList.remove("is-flipped"), 460);
        }
      } else {
        slot.classList.remove("is-flipping");
      }

      if (data.message) showFlash(data.message, "warning");
    } catch (err) {
      // 네트워크·서버 문제면 원래대로 폼을 제출해서 서버 렌더에 맡긴다.
      slot.classList.remove("is-flipping");
      form.submit();
    } finally {
      slot.dataset.busy = "";
    }
  });
})();
