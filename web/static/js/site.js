/* Attestra public site interactions. */

"use strict";

document.addEventListener("DOMContentLoaded", () => {
  /* FAQ accordions */
  document.querySelectorAll(".faq-item").forEach((item) => {
    const q = item.querySelector(".faq-q");
    const a = item.querySelector(".faq-a");
    q.addEventListener("click", () => {
      const open = item.classList.toggle("open");
      a.style.maxHeight = open ? a.scrollHeight + "px" : "0";
    });
  });

  /* Pill tab filtering: [data-tabs-for] pills filter [data-cat] items */
  document.querySelectorAll("[data-tabs-for]").forEach((bar) => {
    const items = document.querySelectorAll(bar.dataset.tabsFor + " [data-cat]");
    bar.querySelectorAll(".pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        bar.querySelectorAll(".pill").forEach((p) => p.classList.remove("active"));
        pill.classList.add("active");
        const want = pill.dataset.filter;
        items.forEach((el) => {
          el.hidden = want !== "All" && el.dataset.cat !== want;
        });
      });
    });
  });

  /* Text search over [data-search] items */
  document.querySelectorAll("[data-search-input]").forEach((input) => {
    const items = document.querySelectorAll(input.dataset.searchInput + " [data-search]");
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      items.forEach((el) => {
        el.hidden = q !== "" && !el.dataset.search.toLowerCase().includes(q);
      });
    });
  });

  /* Show more */
  document.querySelectorAll("[data-show-more]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(btn.dataset.showMore).forEach((el) => (el.hidden = false));
      btn.closest(".offer-more").hidden = true;
    });
  });

  /* Dismissible bars, remembered per browser */
  const dismiss = (key, el, btns) => {
    if (!el) return;
    if (localStorage.getItem(key)) { el.hidden = true; return; }
    btns.forEach((b) => b && b.addEventListener("click", () => {
      localStorage.setItem(key, "1");
      el.hidden = true;
    }));
  };
  dismiss("attestra-promo-closed", document.querySelector(".promo-bar"),
    [document.querySelector(".promo-close")]);
  dismiss("attestra-notice-ok", document.querySelector(".notice-bar"),
    [document.querySelector("[data-notice-accept]")]);
});
