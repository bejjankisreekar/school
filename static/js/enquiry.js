document.addEventListener("DOMContentLoaded", function () {
  const badge = document.getElementById("enquiriesBadge");
  if (!badge) return;

  const url = badge.getAttribute("data-url");
  if (!url) return;

  function setBadge(count) {
    const n = Number(count) || 0;
    if (n <= 0) {
      badge.style.display = "none";
      badge.textContent = "0";
      return;
    }
    badge.style.display = "inline-block";
    badge.textContent = String(n);
  }

  async function update() {
    try {
      const res = await fetch(url, { credentials: "same-origin" });
      if (!res.ok) return;
      const data = await res.json();
      setBadge(data.unread_count);
    } catch (e) {
      // If polling fails, keep the last badge state.
    }
  }

  update();
  setInterval(update, 30000); // every 30 seconds
});

