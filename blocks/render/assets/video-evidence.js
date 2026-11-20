/* Local source videos are linked only when explicitly attached to this book. */
(function () {
  const rows = (window.__DATA__ || {}).video_evidence || [];
  const anchor = document.getElementById('video-evidence-open');
  if (!anchor || !rows.length) return;
  const list = document.createElement('div');
  list.setAttribute('aria-label', '视频证据');
  for (const row of rows) {
    const link = document.createElement('a');
    link.href = row.url;
    link.textContent = '视频证据：' + row.unit_id;
    link.style.marginRight = '0.7em';
    list.appendChild(link);
  }
  anchor.replaceWith(list);
}());
