// Log ao vivo (polling incremental), atualização de estado e atalhos de resposta.
(function () {
  const job = document.getElementById("job");
  const logEl = document.getElementById("log");
  if (job && logEl) {
    const id = job.dataset.job;
    let offset = 0, first = true;
    const follow = document.getElementById("follow");
    async function poll() {
      try {
        const r = await fetch(`/jobs/${id}/log?offset=${offset}`);
        const d = await r.json();
        if (first) { logEl.textContent = ""; first = false; }
        if (d.text) {
          logEl.textContent += d.text;
          if (follow && follow.checked) logEl.scrollTop = logEl.scrollHeight;
        }
        if (!logEl.textContent) logEl.textContent = "(sem saída ainda)";
        offset = d.offset;
      } catch (e) { /* rede instável: tenta de novo no próximo ciclo */ }
    }
    async function status() {
      try {
        const r = await fetch(`/jobs/${id}/status`);
        const s = await r.json();
        if (s.status !== job.dataset.status || s.phase !== job.dataset.phase) location.reload();
      } catch (e) {}
    }
    poll();
    if (logEl.dataset.live === "yes") { setInterval(poll, 2500); setInterval(status, 5000); }
  }
  document.querySelectorAll("[data-fill]").forEach((b) =>
    b.addEventListener("click", () => {
      const input = document.querySelector(`[name="${b.dataset.fill}"]`);
      if (input) { input.value = b.textContent; input.focus(); }
    })
  );
})();
