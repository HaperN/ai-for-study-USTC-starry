/* One shared HTMLAudio clock; voices are pre-generated local files. */
(function () {
  'use strict';
  var current = null, button = null;
  function stop() {
    if (current) { current.pause(); current.currentTime = 0; }
    if (button) { button.textContent = '听图解'; button.setAttribute('aria-pressed', 'false'); }
    current = null; button = null;
  }
  function enhance() {
    var guides = ((window.__DATA__ || {}).study_reader || {}).guides || {};
    document.querySelectorAll('.study-visual[data-study-scene]').forEach(function (card) {
      if (card.querySelector('[data-study-listen]')) return;
      var guide = guides[card.dataset.studyScene], tools = card.querySelector('.study-tools');
      if (!guide || !tools) return;
      var listen = document.createElement('button');
      listen.type = 'button'; listen.dataset.studyListen = '1';
      listen.textContent = '听图解'; listen.setAttribute('aria-label', '播放图像中文讲解');
      listen.setAttribute('aria-pressed', 'false');
      listen.addEventListener('click', function () {
        if (button === listen) { stop(); return; }
        stop();
        var audio = new Audio(guide.audio); current = audio; button = listen;
        listen.textContent = '停止讲解'; listen.setAttribute('aria-pressed', 'true');
        function finish() { if (current === audio) stop(); }
        audio.addEventListener('ended', finish, {once: true});
        audio.addEventListener('error', finish, {once: true});
        var played = audio.play();
        if (played && played.catch) played.catch(finish);
      });
      tools.appendChild(listen);
    });
    if (button && !button.isConnected) stop();
  }
  function boot() {
    var root = document.getElementById('content');
    if (!root) return;
    enhance();
    new MutationObserver(enhance).observe(root, {childList: true, subtree: true});
    document.addEventListener('toggle', function (event) {
      if (button && event.target.tagName === 'DETAILS' && !event.target.open && event.target.contains(button)) stop();
    }, true);
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape') stop(); });
    window.addEventListener('pagehide', stop);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
