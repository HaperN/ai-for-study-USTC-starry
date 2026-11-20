/* Narration uses decoded PCM directly, independent of HTMLMediaElement audio
 * routing. Embedded bytes keep the same playback path working under file://.
 * BufferSource playbackRate follows the video clock but also changes pitch;
 * this adapter does not implement pitch-preserving time stretching. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DSHAudibleAudio = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function createAudio(view, payload) {
    payload = payload || {};
    var events = new view.EventTarget(), context = null, gain = null, buffer = null;
    var loading = null, source = null, timer = null, pending = null, generation = 0;
    var offset = 0, started = 0, rate = 1, volume = 1, muted = false;
    var paused = true, ended = false, disposed = false, error = null;
    function emit(type) { events.dispatchEvent(new view.Event(type)); }
    function abort() { return new view.DOMException('播放已取消', 'AbortError'); }
    function time() {
      var value = offset + (source ? Math.max(0, context.currentTime - started) * rate : 0);
      return buffer ? Math.min(buffer.duration, value) : value;
    }
    function clearTimer() { if (timer !== null) view.clearInterval(timer); timer = null; }
    function stopSource() {
      offset = time();
      if (source) { source.onended = null; try { source.stop(); } catch (_) {} source.disconnect(); source = null; }
      clearTimer();
    }
    function finish() {
      stopSource(); offset = buffer.duration; paused = true; ended = true;
      emit('timeupdate'); emit('pause'); emit('ended');
    }
    function begin() {
      if (offset >= buffer.duration) { finish(); return; }
      source = context.createBufferSource(); source.buffer = buffer;
      source.playbackRate.value = rate; source.connect(gain); started = context.currentTime;
      source.onended = function () { if (!disposed && !paused) finish(); };
      source.start(0, offset);
      clearTimer(); timer = view.setInterval(function () { emit('timeupdate'); }, 100);
    }
    function prepare() {
      if (disposed) return Promise.reject(abort());
      try {
        if (!context) {
          var Context = view.AudioContext || view.webkitAudioContext;
          if (!Context) throw new Error('此浏览器不支持讲解声音');
          context = new Context(); gain = context.createGain(); gain.gain.value = muted ? 0 : volume;
          gain.connect(context.destination);
        }
        // Invoke resume before awaiting fetch/decode so the user's click is retained.
        var resumed = context.resume();
        if (!loading) {
          var bytes;
          if (payload.data_base64) {
            var raw = view.atob(String(payload.data_base64).replace(/^data:[^,]*,/, ''));
            var array = new Uint8Array(raw.length);
            for (var i = 0; i < raw.length; i += 1) array[i] = raw.charCodeAt(i);
            bytes = Promise.resolve(array.buffer);
          } else if (payload.src) {
            bytes = view.fetch(payload.src).then(function (response) {
              if (!response.ok) throw new Error('讲解音频读取失败：' + response.status);
              return response.arrayBuffer();
            });
          } else throw new Error('没有讲解音频');
          loading = bytes.then(function (data) { return context.decodeAudioData(data); }).then(function (decoded) {
            if (disposed) throw abort();
            buffer = decoded; offset = Math.min(offset, buffer.duration);
            emit('loadedmetadata'); emit('durationchange'); emit('canplay'); return decoded;
          });
        }
        return Promise.all([resumed, loading]).then(function () {
          if (disposed) throw abort();
          if (context.state !== 'running') throw new Error('讲解声音尚未启动，请再点一次播放');
          return buffer;
        });
      } catch (failure) { return Promise.reject(failure); }
    }
    function play() {
      var ready = prepare();
      if (disposed) return ready;
      if (!paused && pending) { ready.catch(function () {}); return pending; }
      if (!paused && source) return ready;
      if (ended) offset = 0;
      ended = false; paused = false; error = null;
      var token = ++generation;
      view.document.dispatchEvent(new view.CustomEvent('aiforstudy-audio-start', {detail: {owner: api}}));
      emit('play');
      pending = ready.then(function () {
        if (disposed || paused || token !== generation) throw abort();
        begin(); if (!ended) emit('playing');
      }).catch(function (failure) {
        if (token === generation && !disposed) {
          stopSource(); paused = true;
          if (failure.name !== 'AbortError') { error = failure; emit('error'); }
        }
        throw failure;
      }).finally(function () { if (token === generation) pending = null; });
      return pending;
    }
    function pause() {
      var changed = !paused; generation += 1; pending = null;
      stopSource(); paused = true; if (changed) emit('pause');
    }
    var api = {
      play: play, pause: pause, prepare: prepare,
      addEventListener: events.addEventListener.bind(events),
      removeEventListener: events.removeEventListener.bind(events),
      dispose: function () {
        if (disposed) return; pause(); disposed = true;
        if (gain) gain.disconnect();
        if (context && context.state !== 'closed') context.close().catch(function () {});
        buffer = null;
      }
    };
    Object.defineProperties(api, {
      currentTime: {get: time, set: function (value) {
        value = Number(value); if (!Number.isFinite(value) || value < 0) throw new TypeError('播放时间无效');
        var running = !!source; stopSource(); offset = buffer ? Math.min(value, buffer.duration) : value; ended = false;
        emit('seeking'); if (running && !paused) begin(); emit('timeupdate'); emit('seeked');
      }},
      duration: {get: function () { return buffer ? buffer.duration : NaN; }},
      paused: {get: function () { return paused; }}, ended: {get: function () { return ended; }},
      error: {get: function () { return error; }},
      playbackRate: {get: function () { return rate; }, set: function (value) {
        value = Number(value); if (!Number.isFinite(value) || value <= 0) throw new TypeError('播放速度无效');
        if (value === rate) return;
        var running = !!source; stopSource(); rate = value; if (running && !paused) begin(); emit('ratechange');
      }},
      volume: {get: function () { return volume; }, set: function (value) {
        value = Number(value); if (!Number.isFinite(value) || value < 0 || value > 1) throw new TypeError('音量无效');
        volume = value; if (gain) gain.gain.value = muted ? 0 : volume; emit('volumechange');
      }},
      muted: {get: function () { return muted; }, set: function (value) {
        muted = !!value; if (gain) gain.gain.value = muted ? 0 : volume; emit('volumechange');
      }}
    });
    return api;
  }

  function attachVideo(video, playback) {
    var view = video.ownerDocument.defaultView, audio = createAudio(view, playback.audio);
    var disposed = false, revision = 0, intent = 0, listeners = [], reported = null;
    video.src = playback.video_src;
    function pause() { revision += 1; intent += 1; video.pause(); audio.pause(); }
    function visible() {
      if (!video.isConnected) return false;
      for (var node = video; node; node = node.parentElement) {
        if (node.hidden || node.tagName === 'DETAILS' && !node.open) return false;
      }
      return true;
    }
    function available() {
      if (disposed) return false;
      if (visible()) return true;
      pause(); return false;
    }
    // The media element remains paused while decoding, so hiding it does not
    // necessarily emit a native pause event. Invalidate queued playback here.
    var observer = new view.MutationObserver(function () { if (!disposed && !visible()) pause(); });
    observer.observe(video.ownerDocument.documentElement, {
      subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'open']
    });
    function on(target, name, callback) {
      target.addEventListener(name, callback); listeners.push([target, name, callback]);
    }
    function fail(failure) {
      if (disposed || failure.name === 'AbortError') return;
      video.pause(); audio.pause();
      if (reported === failure) return;
      reported = failure;
      video.dispatchEvent(new view.Event('error'));
    }
    function settings() {
      audio.playbackRate = video.playbackRate;
      audio.volume = video.volume; audio.muted = video.muted;
    }
    function sync() {
      if (!available() || video.paused || video.ended || video.seeking || video.readyState < 3) return Promise.resolve();
      settings();
      if (audio.paused || Math.abs(audio.currentTime - video.currentTime) > 0.18) audio.currentTime = video.currentTime;
      return audio.play();
    }
    function start() {
      if (!available()) return Promise.resolve();
      var token = revision;
      // This call is synchronous with the native play event, retaining activation.
      return audio.prepare().then(function () { if (!disposed && token === revision) return sync(); });
    }
    on(video, 'play', function () { start().catch(fail); });
    on(video, 'playing', function () { start().catch(fail); });
    on(video, 'pause', function () { revision += 1; intent += 1; audio.pause(); });
    on(video, 'waiting', function () { revision += 1; audio.pause(); });
    on(video, 'seeking', function () { revision += 1; audio.pause(); });
    on(video, 'seeked', function () {
      audio.currentTime = video.currentTime;
      if (!video.paused) start().catch(fail);
    });
    on(video, 'ratechange', settings); on(video, 'volumechange', settings);
    on(video, 'timeupdate', function () {
      if (!audio.paused && !video.paused && !video.seeking && video.readyState >= 3 &&
          Math.abs(audio.currentTime - video.currentTime) > 0.18) audio.currentTime = video.currentTime;
    });
    on(video, 'ended', function () { revision += 1; audio.pause(); });
    on(video, 'error', function () { revision += 1; audio.pause(); });
    on(audio, 'error', function () { fail(audio.error || new Error('讲解声音播放失败')); });
    on(view.document, 'aiforstudy-audio-start', function (event) {
      if (event.detail && event.detail.owner !== audio) { intent += 1; video.pause(); audio.pause(); }
    });
    settings();
    return {
      audio: audio,
      prepare: function () {
        return available() ? audio.prepare() : Promise.reject(new view.DOMException('播放已取消', 'AbortError'));
      },
      play: function () {
        if (!available()) return Promise.reject(new view.DOMException('播放已取消', 'AbortError'));
        reported = null;
        var token = ++intent;
        // Decode first, then start both clocks. Native waiting/seeking events
        // suspend the narration until the video really resumes.
        return audio.prepare().then(function () {
          if (!available() || token !== intent) throw new view.DOMException('播放已取消', 'AbortError');
          return video.play();
        }).then(sync).catch(function (failure) { fail(failure); throw failure; });
      },
      pause: pause,
      dispose: function () {
        if (disposed) return; disposed = true; revision += 1;
        observer.disconnect();
        listeners.forEach(function (row) { row[0].removeEventListener(row[1], row[2]); });
        listeners = []; video.pause(); audio.dispose();
      }
    };
  }

  return {createAudio: createAudio, attachVideo: attachVideo};
});
