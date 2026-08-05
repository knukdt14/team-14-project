// 최소한의 점진적 개선용 JS. 핵심 기능은 전부 폼/링크(GET·POST)만으로 동작한다.
document.addEventListener("DOMContentLoaded", () => {
  // 찜/선택 등 폼 제출 중 중복 클릭 방지
  document.querySelectorAll("form[data-once]").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[type=submit]");
      if (btn) {
        btn.disabled = true;
        btn.dataset.originalText = btn.textContent;
        btn.textContent = "처리 중...";
      }
    });
  });

  // flash 메시지 몇 초 뒤 자동으로 옅어지기
  document.querySelectorAll(".flash-stack .flash").forEach((el, i) => {
    setTimeout(() => {
      el.style.transition = "opacity .4s ease";
      el.style.opacity = "0.55";
    }, 4000 + i * 200);
  });
});
