/* 教材页动画播放器（计划 §T09）。
 *
 * 接口（与计划逐字一致）：
 *     mountAnimations(root, payload) -> dispose
 *     dispose.selectStep(index, {autoplay: true}) -> boolean
 *     dispose.player emits bubbling stepchange {unit_id, request_id, step_id, index}
 *
 * 三条结构约束，写在这里是因为它们最容易在"顺手加个功能"时被破坏：
 *
 * 1. **只读本地 manifest**。播放器不发任何请求：不 fetch、不 XHR、不认识管理 API。
 *    资源地址只从 `payload.resources[*].relative_path` 拼出来（相对本页）。
 *    —— 教材页跑在 `sandbox="allow-scripts"` 的不透明 origin 里，任何"回管理端问一声"
 *    的设计都会同时破坏隔离与离线。
 * 2. **属性白名单**。所有节点用 createElement + 显式 setAttribute 造，不用 innerHTML
 *    拼字符串，也不接受 manifest 里的任意属性。事件属性（on*）永远进不来。
 * 3. **缺资源不静默消失**。缺 mp4／字幕时显示一条错误 + 文字稿，并且**不许**挂一个
 *    空 src 的 <video> 假装在播。
 *
 * 4. **字幕在不透明 origin 下不能指 .vtt 文件**（T10 实测）：生产里教材页跑在
 *    `sandbox="allow-scripts"` 的 iframe 中，文档 origin 是不透明的 `null`。此时
 *    `<track src="assets/animations/captions.vtt">` 会被 Chrome 以
 *    「Unsafe attempt to load URL … Domains, protocols and ports must match」拒绝
 *    （`crossorigin="anonymous"` 也一样，见 T10 证据 `track-blocker-isolation.json`）。
 *    所以：**不透明 origin 下**把构建期写进载荷的 `captions_text`（与盘上的
 *    `captions.vtt` 逐字节相同）造成 `blob:` URL 给 `<track>` 用 —— 不经网络、
 *    不需要 CORS，也不放宽任何工作台权限；**透明 origin 下**仍然直接读真 `.vtt`
 *    文件（那样浏览器读到的就是交付包里那一份）。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require('./study-math.js'), require('./audible-audio.js'));
  } else {
    root.DSHAnimations = factory(root.DSHStudyMath, root.DSHAudibleAudio);
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (studyMath, audibleAudio) {
  "use strict";

  var REQUIRED = ["mp4", "poster", "captions", "transcript"];
  var REQUIRED_LABEL = {mp4: "视频", poster: "封面", captions: "字幕", transcript: "文字稿"};

  /* 允许出现在播放器 DOM 上的属性。**唯一一处定义** —— 加属性要改这里。 */
  var ATTRS = ["src", "alt", "poster", "preload", "controls", "playsinline", "kind", "srclang", "label", "default",
               "crossorigin", "class", "id", "type", "aria-label", "role", "aria-current",
               "aria-hidden", "aria-expanded", "aria-pressed", "aria-live", "tabindex", "title", "href", "target", "rel", "hidden", "value",
               "data-step", "data-action", "data-animation-player", "data-animation-status",
               "data-animation-error", "data-animation-controls", "data-animation-transcript",
               "data-animation-caption", "data-animation-poster",
               "data-resource-kind", "data-source", "for", "selected", "disabled",
               /* 播放器建节点时本来就写了 `data-unit` / `data-request`（见下面 element(...)），
                  但白名单里没有它们 → 属性被**静默丢掉**，页面上再也认不出这是哪一节、
                  哪一次请求的动画。T10 抓到的这个缺口：白名单与写入意图不一致。
                  补进来之后"所选单元恰好 1"才能在真实 DOM 上被指认（T10 验收项）。 */
               "data-unit", "data-request", "min", "max", "step"];

  function element(doc, tag, attrs, text) {
    var node = doc.createElement(tag);
    Object.keys(attrs || {}).forEach(function (name) {
      var value = attrs[name];
      if (value === null || value === undefined || value === false) return;
      if (ATTRS.indexOf(name) < 0) return;            // 白名单之外**丢掉**，不报错也不带上
      if (/^on/i.test(name)) return;
      node.setAttribute(name, String(value));
    });
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  // Render page captions as text plus local KaTeX nodes. WebVTT and the
  // manifest strings remain untouched; native video captions are plain text.
  function captionText(node, value) {
    var text = String(value == null ? "" : value), doc = node.ownerDocument;
    var katex = doc.defaultView && doc.defaultView.katex;
    node.textContent = "";
    function literal(part) { if (part) node.appendChild(doc.createTextNode(part)); }
    if (!katex || typeof katex.render !== "function") { literal(text); return; }
    function math(source, original, display) {
      var span = element(doc, "span", {"class": "animation-math"});
      try {
        katex.render(studyMath?studyMath.operatorLimits(source):source, span, {displayMode:display, throwOnError:true, trust:false,
          strict:"ignore", maxExpand:1000, maxSize:20});
        node.appendChild(span);
      } catch (err) { literal(original); }
    }
    function bare(part) {
      // A bare expression must have a math marker and no ordinary words or
      // unknown commands. Chinese prose/punctuation, timestamps and paths
      // remain text; adjacent factors and both sides of '=' stay together.
      // Math symbols such as − (U+2212), Greek letters, ∫ Σ √ ∞ → and
      // Unicode super/subscripts belong to the same expression; without them
      // `|z_n−z0|<δ` used to split into half-rendered fragments.
      var chunks = /\b\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\b|[A-Za-z0-9\\_^{}=+\-*\/().,'|<>≠≤≥±×−·∓≈≡∼∝→←↦⇒⇔∞∂∇∫∮∑∏√∈∉⊂⊆∪∩°′″α-ωΑ-Ωϑϕϵ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾ⁿ₀₁₂₃₄₅₆₇₈₉₊₋]+(?:[ \t]+[A-Za-z0-9\\_^{}=+\-*\/().,'|<>≠≤≥±×−·∓≈≡∼∝→←↦⇒⇔∞∂∇∫∮∑∏√∈∉⊂⊆∪∩°′″α-ωΑ-Ωϑϕϵ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾ⁿ₀₁₂₃₄₅₆₇₈₉₊₋]+)*/g;
      var cursor = 0, found;
      while ((found = chunks.exec(part))) {
        literal(part.slice(cursor, found.index));
        var raw = found[0];
        // Do not swallow the hour/minute prefix of a following timestamp into
        // an arrow or formula chunk (for example "--> 00:04").
        var timeTail=raw.match(/[ \t]+\d{1,2}$/);
        if(timeTail&&part.charAt(chunks.lastIndex)===':'){raw=raw.slice(0,-timeTail[0].length);chunks.lastIndex-=timeTail[0].length;}
        var expr = raw.replace(/[., \t]+$/, ""), known = true;
        var letters = expr.replace(/\\([A-Za-z]+)/g, function (_, command) {
          if (!/^(?:arcsin|arccos|arctan|geqslant|leqslant|ll|gg|circ|angle|perp|parallel|forall|exists|setminus|emptyset|varnothing|Leftrightarrow|leftrightarrow|iff|longrightarrow|mid|vert|langle|rangle|ell|bigl|bigr|Bigl|Bigr|displaystyle|boldsymbol|mathbf|mathcal|widehat|widetilde|underline|binom|neg|cot|sec|csc|max|min|sup|inf|det|deg|bar|overline|hat|tilde|vec|frac|dfrac|tfrac|sqrt|cdot|cdots|ldots|dots|times|div|pm|mp|ne|neq|le|leq|ge|geq|approx|equiv|sim|to|mapsto|rightarrow|leftarrow|Rightarrow|in|notin|subset|cup|cap|left|right|big|Big|quad|mathrm|mathbb|operatorname|ln|log|exp|sin|cos|tan|sinh|cosh|tanh|arg|Arg|Ln|Re|Im|lim|sum|prod|int|oint|partial|nabla|prime|alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega|infty)$/.test(command)) known = false;
          return "";
        });
        // Function names may lack a backslash (cos4φ); short letter runs are
        // implicit products (nz^{n−1}); four or more letters are prose.
        var functions = /(^|[^\\A-Za-z])(arcsin|arccos|arctan|sinh|cosh|tanh|sin|cos|tan|cot|exp|log|ln|arg|Arg|Ln|Re|Im|lim|max|min)(?![A-Za-z])/g;
        if (known && /[A-Za-z0-9α-ωΑ-Ω∞∂∫∮∑∏√]/.test(expr) && /[_^\\=<>≠≤≥∞∂∫∮∑∏√∈α-ωΑ-Ω⁰-⁹₀-₉]/.test(expr) && !/[A-Za-z]{4,}/.test(letters.replace(functions, "$1"))) {
          math(expr.replace(functions, "$1\\$2"), expr, false); literal(raw.slice(expr.length));
        } else literal(raw);
        cursor = chunks.lastIndex;
      }
      literal(part.slice(cursor));
    }
    function escaped(index) {
      var slashes = 0;
      while (index > 0 && text.charAt(--index) === "\\") slashes++;
      return slashes % 2 === 1;
    }
    var delimiters = /\$\$|\$|\\\(|\\\[/g, cursor = 0, found;
    while ((found = delimiters.exec(text))) {
      if (escaped(found.index)) continue;
      var open = found[0], close = open === "\\(" ? "\\)" : open === "\\[" ? "\\]" : open;
      var start = delimiters.lastIndex, end = text.indexOf(close, start);
      while (end >= 0 && escaped(end)) end = text.indexOf(close, end + close.length);
      if (end < 0) continue;
      bare(text.slice(cursor, found.index));
      math(text.slice(start, end), text.slice(found.index, end + close.length), open === "$$" || open === "\\[");
      cursor = end + close.length; delimiters.lastIndex = cursor;
    }
    bare(text.slice(cursor));
  }

  function number(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : fallback;
  }

  function baseOf(payload) {
    var base = payload && payload.base ? String(payload.base) : "";
    if (base && base.charAt(base.length - 1) !== "/") base += "/";
    return base;
  }

  /** 资源地址：**只由本地 manifest 的相对路径拼出来**，不接受绝对 URL。 */
  function resourceUrl(payload, kind) {
    var row = (payload && payload.resources && payload.resources[kind]) || null;
    if (!row || !row.relative_path) return null;
    var relative = String(row.relative_path);
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(relative) || relative.indexOf("//") === 0) return null;
    return baseOf(payload) + relative.replace(/^\.?\//, "");
  }

  /** 由步骤字幕合成一份 WebVTT（`captions_text` 缺失时的兜底）。 */
  function webvttFromSteps(steps) {
    var rows = ["WEBVTT", ""];
    steps.forEach(function (step, index) {
      // Cue text goes on its own line; `<` and `&` would start VTT markup.
      rows.push("step-" + (index + 1), stamp(step.start) + " --> " +
                stamp(step.end == null ? step.start + 4 : step.end),
                String(step.caption).replace(/&/g, "&amp;").replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;").replace(/\n{2,}/g, "\n"), "");
    });
    return rows.join("\n") + "\n";
  }

  function stamp(seconds) {
    var value = Math.max(0, number(seconds, 0));
    var hours = Math.floor(value / 3600);
    var minutes = Math.floor((value % 3600) / 60);
    var rest = value % 60;
    var whole = Math.floor(rest);
    var millis = Math.round((rest - whole) * 1000);
    function pad(n, width) { return ("000" + n).slice(-width); }
    return pad(hours, 2) + ":" + pad(minutes, 2) + ":" + pad(whole, 2) + "." + pad(millis, 3);
  }

  function isOpaqueOrigin() {
    try {
      return String((typeof window !== "undefined" && window.origin) || "") === "null";
    } catch (err) { return false; }
  }

  /** `<track>` 到底读哪儿：返回 `{url, from_payload}`。 */
  function captionsSource(payload, fileUrl, steps) {
    if (!fileUrl) return {url: null, from_payload: false};
    if (!isOpaqueOrigin()) return {url: fileUrl, from_payload: false};
    var text = String((payload && payload.captions_text) || "");
    /* 没有 `captions_text` 时按步骤字幕合成；**步骤也为空就退回真文件** ——
       拿一份没有 cue 的合成 VTT 顶上去，只会让"字幕读不到"变成一个更难查的假象。 */
    if (!text && (steps || []).length) text = webvttFromSteps(steps);
    if (!text) return {url: fileUrl, from_payload: false};
    try {
      return {url: URL.createObjectURL(new Blob([text], {type: "text/vtt"})),
              from_payload: true};
    } catch (err) { return {url: fileUrl, from_payload: false}; }
  }

  function stepsOf(payload) {
    var rows = (payload && payload.steps) || [];
    return rows.map(function (row, index) {
      var caption = String(row.display_markdown || row.caption_tex || row.caption || row.text || "");
      var title = typeof row.display_title === "string" && row.display_title.trim() ||
                  typeof row.title === "string" && row.title.trim() ||
                  typeof row.label === "string" && row.label.trim();
      // Use complete authored text only. Truncating a caption can split a
      // formula or change its meaning; long captions remain in the transcript.
      if (!title && caption.trim() && Array.from(caption.trim()).length <= 24) title = caption.trim();
      return {
        id: String(row.step_id || ("s" + (index + 1))),
        title: title || ("第 " + (index + 1) + " 步"),
        caption: caption || ("第 " + (index + 1) + " 步"),
        start: Math.max(0, number(row.start, 0)),
        end: number(row.end, null),
      };
    });
  }

  function missingKinds(payload) {
    var present = (payload && payload.resources) || {};
    var declared = (payload && payload.missing) || [];
    var missing = [];
    REQUIRED.forEach(function (kind) {
      if (!present[kind] || !present[kind].relative_path) missing.push(kind);
    });
    declared.forEach(function (kind) {
      if (missing.indexOf(kind) < 0) missing.push(kind);
    });
    return missing;
  }

  function formatTime(seconds) {
    var value = Math.max(0, number(seconds, 0));
    var whole = Math.floor(value);
    var minutes = Math.floor(whole / 60);
    var rest = whole % 60;
    return minutes + ":" + (rest < 10 ? "0" : "") + rest;
  }

  function mountAnimations(root, payload, options) {
    options=options||{};
    if (!root || typeof root.appendChild !== "function") return function () {};
    var doc = root.ownerDocument || (typeof document !== "undefined" ? document : null);
    if (!doc) return function () {};

    var data = payload || {};
    var steps = stepsOf(data);
    var missing = missingKinds(data);
    var broken = missing.length > 0;
    var videoUrl = broken && missing.indexOf("mp4") >= 0 ? null : resourceUrl(data, "mp4");
    var posterUrl = missing.indexOf("poster") >= 0 ? null : resourceUrl(data, "poster");
    var captionsUrl = missing.indexOf("captions") >= 0 ? null : resourceUrl(data, "captions");
    var blobCaptions = null;
    var listeners = [];
    var disposed = false;
    var current = -1;
    var transcriptShown = broken;
    var activeStep = -1;
    var stepComplete = false;
    var pendingStep = -1;
    var pendingAutoplay = true;
    var continuous = false;
    var frame = null;
    var checkingBoundary = false;
    var view = doc.defaultView;

    var player = element(doc, "figure", {
      "class": "animation-player" + (broken ? " has-error" : ""),
      "data-animation-player": "",
      "data-unit": String(data.unit_id || ""),
      "data-request": String(data.request_id || ""),
      "role": "group",
      "aria-label": "教学动画",
    });

    /* ── 舞台：video + poster + track ───────────────────────────────── */
    var stage = element(doc, "div", {"class": "animation-stage media-viewport"});
    var stagebar = element(doc, "div", {"class": "animation-stagebar"});
    var stageTitle = element(doc, "span", {"class": "animation-stage-title"}, "观察动画");
    var fullscreenButton = element(doc, "button", {"type":"button", "class":"animation-fullscreen",
      "data-action":"fullscreen", "aria-label":"全屏查看动画", "disabled":videoUrl ? null : true}, "⛶");
    stagebar.appendChild(stageTitle);stagebar.appendChild(fullscreenButton);stage.appendChild(stagebar);
    var video = null;
    var audioDriver = null;
    var posterButton = null;
    var captionLayer = null;
    var captionsOn = true;
    var captionShown = -2;
    var playedOnce = false;  // captions start with playback, never over the poster
    if (videoUrl) {
      video = element(doc, "video", {
        "class": "animation-video",
        "src": videoUrl,
        "poster": posterUrl,
        "preload": posterUrl ? "none" : "metadata",
        "playsinline": "",
        "aria-label": "教学动画视频",
      });
      if (captionsUrl) {
        var captions = captionsSource(data, captionsUrl, steps);
        captionsUrl = captions.url;
        if (captions.from_payload) blobCaptions = captionsUrl;
        var track = element(doc, "track", {
          "kind": "captions",
          "src": captionsUrl,
          "srclang": "zh",
          "label": "中文字幕",
          "default": "",
        });
        /* 不透明 origin（sandbox allow-scripts 且**没有** allow-same-origin）下，
           <track> 是按 CORS 规则取的。`crossorigin` 让浏览器把这条记成 CORS 请求，
           服务端给的那一条只读许可才生效；不给这个属性时读取行为在各浏览器里
           不一致（T09 用真实 Chromium 实测过，见证据目录）。
           注意：它只影响**这一条字幕请求**，不放宽工作台任何权限。 */
        if (!captions.from_payload) track.setAttribute("crossorigin", "anonymous");
        video.appendChild(track);
      }
      if (data.playback && data.playback.video_src && data.playback.audio) {
        var audioAPI = audibleAudio || view && view.DSHAudibleAudio;
        if (audioAPI) audioDriver = audioAPI.attachVideo(video, data.playback);
      }
      var viewport = element(doc, "div", {"class":"animation-viewport"});
      viewport.appendChild(video);
      if (posterUrl) {
        var hash = String(((data.resources || {}).poster || {}).sha256 || "").replace(/^sha256:/, "");
        var imageUrl = posterUrl + (/^[a-f0-9]{64}$/i.test(hash) ? (posterUrl.indexOf("?") < 0 ? "?v=" : "&v=") + hash.slice(0,12) : "");
        posterButton = element(doc, "button", {"class":"animation-poster", "type":"button",
          "data-animation-poster":"", "aria-label":"播放当前这一步的动画"});
        posterButton.appendChild(element(doc, "img", {"src":imageUrl, "alt":""}));
        posterButton.appendChild(element(doc, "span", {"class":"animation-poster-play"}, "▶ 播放示范"));
        viewport.appendChild(posterButton);
      }
      stage.appendChild(viewport);
      // Plan R1: the player draws captions itself so formulas render with
      // KaTeX; the native track stays loaded but hidden (it is plain text).
      captionLayer = element(doc, "div", {"class": "animation-caption-layer",
        "data-animation-caption": "", "aria-live": "polite", "hidden": ""});
      stage.appendChild(captionLayer);
    } else {
      stage.appendChild(element(doc, "div", {"class": "animation-placeholder"},
                                "没有可播放的视频"));
    }
    player.appendChild(stage);
    var details = element(doc, "div", {"class": "media-details"});
    player.appendChild(details);

    /* ── 错误条：缺件必须看得见 ────────────────────────────────────── */
    var error = null;
    if (broken) {
      error = element(doc, "p", {
        "class": "animation-error", "data-animation-error": "", "role": "alert",
      }, "这一段动画缺 " + missing.map(function (kind) {
        return REQUIRED_LABEL[kind] || kind;
      }).join("、") + "（" + missing.join("/") + "）：下面是同一份文字稿，先按文字看。");
      details.appendChild(error);
    }

    /* ── 状态行 ───────────────────────────────────────────────────── */
    var status = element(doc, "p", {
      "class": "animation-status", "data-animation-status": "", "aria-live": "polite",
    }, broken ? "资源不完整" : (steps.length ? "第 1 / " + steps.length + " 步" : "无步骤"));
    /* ── 控件 ─────────────────────────────────────────────────────── */
    var controls = element(doc, "div", {
      "class": "animation-controls animation-controls-wrap", "data-animation-controls": "", "role": "toolbar",
    });

    function button(action, label, text) {
      return element(doc, "button", {
        "type": "button", "class": "animation-button", "data-action": action,
        "aria-label": label, "disabled": video ? null : true,
      }, text);
    }

    var actionRow = element(doc, "div", {"class":"animation-action-row"});
    var currentAction = element(doc, "p", {"class":"animation-current-action"}, "当前动作 · ");
    var actionTitle = element(doc, "strong", {"class":"animation-action-title"}, "观察动画");
    currentAction.appendChild(actionTitle);actionRow.appendChild(currentAction);details.appendChild(actionRow);
    var playButton = button("play", steps.length ? "播放这一步" : "播放", options.compact?'播放':steps.length ? "播放这一步" : "播放");
    playButton.classList.add("animation-primary");
    var replayButton = button("replay", "重播这一步", "重播");
    replayButton.classList.add("animation-replay");
    controls.appendChild(playButton);
    controls.appendChild(replayButton);
    var nextButton = null;
    var playAllButton = null;
    if (steps.length) {
      nextButton = button("next", "播放下一步", options.compact?'下一段':"下一步");
      playAllButton = button("play-all", "从当前位置连续播放", "连续播放");
      playAllButton.classList.add("animation-continuous");
      playAllButton.setAttribute("aria-pressed", "false");
      controls.appendChild(nextButton);
      actionRow.appendChild(playAllButton);
    }
    details.appendChild(controls);

    var settings = element(doc, "details", {"class":"animation-settings"});
    settings.appendChild(element(doc, "summary", {}, "播放设置"));
    var settingsBody = element(doc, "div", {"class":"animation-settings-body"});
    settings.appendChild(settingsBody);

    var speed = element(doc, "select", {
      "class": "animation-speed", "data-action": "speed",
      "aria-label": "倍速", "disabled": video ? null : true,
    });
    [0.5, 0.75, 1, 1.25, 1.5, 2].forEach(function (rate) {
      speed.appendChild(element(doc, "option", {
        "value": String(rate), "selected": rate === 1 ? "" : null,
      }, rate + "×"));
    });
    var speedField = element(doc, "label", {"class": "animation-field"}, "倍速");
    speedField.appendChild(speed);settingsBody.appendChild(speedField);

    // Narration is embedded in this video, so pause, seek and speed use one clock.
    var volume = element(doc, "input", {"type":"range", "min":"0", "max":"1", "step":"0.05",
      "value":"1", "data-action":"volume", "aria-label":"讲解音量", "disabled":video ? null : true});
    var volumeField = element(doc, "label", {"class":"animation-field"}, "音量");
    volumeField.appendChild(volume);settingsBody.appendChild(volumeField);
    var muteButton = button("mute", "静音讲解", "静音");
    muteButton.setAttribute("aria-pressed", "false");settingsBody.appendChild(muteButton);

    var captionsButton = element(doc, "button", {
      "type": "button", "class": "animation-button", "data-action": "captions",
      "aria-label": "字幕开关", "aria-pressed": "true",
      "disabled": captionsUrl ? null : true,
    }, "字幕");
    settingsBody.appendChild(captionsButton);
    if(options.compact){
      settingsBody.appendChild(replayButton);
      if(playAllButton)settingsBody.appendChild(playAllButton);
      actionRow.hidden=true;
    }

    var transcriptButton = element(doc, "button", {
      "type": "button", "class": "animation-button animation-transcript-toggle", "data-action": "transcript",
      "aria-label": "文字稿开关", "aria-expanded": transcriptShown ? "true" : "false",
    }, "查看动画文字稿");

    if (steps.length) {
      var stepBar = element(doc, "div", {"class": "animation-steps", "role": "group",
                                         "aria-label": "点击播放一步，结束后自动暂停"});
      steps.forEach(function (step, index) {
        var stepButton = element(doc, "button", {
          "type": "button", "class": "animation-step", "data-step": String(index),
          "data-action": "step", "aria-label": "播放第 " + (index + 1) + " 步：" + step.title,
          "disabled": video ? null : true,
        });
        stepButton.appendChild(element(doc, "span", {"class":"animation-step-dot", "aria-hidden":"true"}));
        var stepLabel = element(doc, "span", {"class":"animation-step-label"});
        captionText(stepLabel, step.title);stepButton.appendChild(stepLabel);stepBar.appendChild(stepButton);
      });
      if(options.compact){var directory=element(doc,'details',{'class':'animation-step-directory'});directory.appendChild(element(doc,'summary',{},'选择讲解片段（'+steps.length+'段）'));directory.appendChild(stepBar);details.appendChild(directory);}else details.appendChild(stepBar);
    }
    details.appendChild(status);

    /* ── 文字稿（缺件时的兜底，也是无障碍的正文） ──────────────────── */
    var transcriptHost = null;
    if (steps.length || data.transcript_text) {
      details.appendChild(transcriptButton);
      var rows = element(doc, "ol", {"class": "animation-transcript-list"});
      steps.forEach(function (step, index) {
        var item = element(doc, "li", {"class": "animation-transcript-row", "data-step": String(index)});
        item.appendChild(element(doc, "button", {
          "type": "button", "class": "animation-transcript-time", "data-action": "step",
          "data-step": String(index), "aria-label": "播放第 " + (index + 1) + " 步",
        }, formatTime(step.start)));
        var caption = element(doc, "span", {"class": "animation-transcript-text", "data-source": "step:" + step.id});
        captionText(caption, step.caption); item.appendChild(caption);
        rows.appendChild(item);
      });
      if (!steps.length && data.transcript_text) {
        var transcript = element(doc, "li", {"class": "animation-transcript-row", "data-source": "transcript"});
        var transcriptText = element(doc, "span", {"class": "animation-transcript-text"});
        captionText(transcriptText, data.transcript_text); transcript.appendChild(transcriptText); rows.appendChild(transcript);
      }
      transcriptHost = element(doc, "section", {
        "class": "animation-transcript", "data-animation-transcript": "",
        "aria-label": "文字稿", "hidden": transcriptShown ? null : "",
      });
      transcriptHost.appendChild(element(doc, "h3", {"class": "animation-transcript-title"},
                                         "讲解文字稿"));
      transcriptHost.appendChild(rows);
      details.appendChild(transcriptHost);
    }
    details.appendChild(settings);

    /* ── 行为 ─────────────────────────────────────────────────────── */
    function on(node, type, handler) {
      if (!node || !node.addEventListener) return;
      node.addEventListener(type, handler);
      listeners.push([node, type, handler]);
    }

    function setStatus(text) { captionText(status, text); }
    function playbackHint() {
      if(options.compact&&!playedOnce&&!broken){setStatus('');return;}
      if (continuous) setStatus(video && video.paused ? "连续播放已暂停，可继续播放或选择一个动作。" : "连续播放中，可随时暂停或点击动作返回逐步播放。");
      else if (stepComplete) setStatus("本步已结束，画面停留供你对照。");
      else if (steps.length) setStatus("第 " + (current + 1) + " / " + steps.length + " 个动作 · 播放到本步末尾自动暂停");
      else setStatus(video && !video.paused ? "播放中" : "点击播放查看整段动画");
    }

    /* ⚠️ 真浏览器里字幕状态在 `<track>` 元素的 `.track`（TextTrack）上，不在元素自己身上。
       旧写法 `track.mode = ...` 只是给元素挂了一个同名的普通属性：**开关看着生效、其实没关**
       （T09 的 jsdom 断言看不出来，因为 jsdom 没有 TextTrack 实现）。
       T10 用真实 Chromium 量到这一点，这里改成优先用 TextTrack，缺失时退回元素。 */
    function trackState(video) {
      var el = video && video.querySelector ? video.querySelector("track") : null;
      if (!el) return null;
      return el.track || el;
    }

    function highlight(index, force) {
      if (index === current && !force) return;
      current = index;
      Array.prototype.forEach.call(player.querySelectorAll("[data-step]"), function (node) {
        var isCurrent = Number(node.getAttribute("data-step")) === index;
        if (isCurrent) node.setAttribute("aria-current", "true");
        else node.removeAttribute("aria-current");
        if (node.classList.contains("animation-step")) {
          node.classList.toggle("is-current", isCurrent);
          node.classList.toggle("is-past", Number(node.getAttribute("data-step")) < index);
        }
      });
      if (index >= 0 && steps[index]) {
        captionText(stageTitle, options.compact&&!playedOnce&&options.title ? '预览 · '+options.title : steps[index].title);
        captionText(actionTitle, steps[index].title);
        playbackHint();
        if (view && typeof view.CustomEvent === "function") player.dispatchEvent(new view.CustomEvent("stepchange", {
          bubbles:true, detail:{unit_id:String(data.unit_id || ""), request_id:String(data.request_id || ""),
            step_id:steps[index].id, index:index}
        }));
      }
      if (nextButton) nextButton.disabled = !video || index >= steps.length - 1;
    }

    function seek(time) {
      if (!video) return;
      /* 步骤 seek 误差 ≤0.2s（§T09）。写法上直接给精确值：
         不要"算一帧"或四舍五入到 0.5s —— 那样误差会超过判据。 */
      try { video.currentTime = time; } catch (err) { /* 元数据尚未就绪时稍后重试 */ }
    }

    function cancelFrame() {
      if (frame !== null && view && view.cancelAnimationFrame) view.cancelAnimationFrame(frame);
      frame = null;
    }

    function setPlayLabel(label) {
      playButton.textContent = options.compact?(label==='暂停'?'暂停':'播放'):label;
      playButton.setAttribute("aria-label", label);
    }

    function updatePausedState() {
      cancelFrame();
      player.removeAttribute("data-playing");
      setPlayLabel(stepComplete && activeStep >= 0 ? "重播这一步" : continuous ? "继续播放" : steps.length ? "播放这一步" : "播放");
      playbackHint();
    }

    // Existing time stamps are the source of truth. An omitted end can use
    // the next step or actual video duration; never invent a fixed duration.
    function stepBounds(index) {
      var step = steps[index];
      if (!step || !video) return null;
      var start = step.start, end = step.end;
      var duration = number(video.duration, null);
      if (duration !== null && duration > 0 && start >= duration) return null;
      if (end === null || end <= start) end = null;
      for (var next = index + 1; next < steps.length; next += 1) {
        if (steps[next].start > start) {
          end = end === null ? steps[next].start : Math.min(end, steps[next].start);
          break;
        }
      }
      if (duration !== null && duration > 0) end = end === null ? duration : Math.min(end, duration);
      return end !== null && end > start ? {start: start, end: end} : null;
    }

    function stopAtBoundary() {
      if (!video || activeStep < 0 || stepComplete || checkingBoundary) return false;
      var bounds = stepBounds(activeStep);
      if (!bounds) return false;
      if (number(video.currentTime, 0) < bounds.end) return false;
      checkingBoundary = true;
      stepComplete = true;
      cancelFrame();
      pauseVideo();
      seek(bounds.end);
      setPlayLabel("重播这一步");
      playbackHint();
      checkingBoundary = false;
      return true;
    }

    // timeupdate can be sparse. While a step is playing, check each display
    // frame too; pause/dispose cancels this one per-player callback.
    function scheduleFrame() {
      if (disposed || !video || video.paused || activeStep < 0 || stepComplete || frame !== null) return;
      if (view && view.requestAnimationFrame) frame = view.requestAnimationFrame(function () {
        frame = null;
        if (!stopAtBoundary()) scheduleFrame();
      });
    }

    function pauseVideo() {
      if (audioDriver) audioDriver.pause();
      else if (video) video.pause();
    }

    function prepareAudio() {
      if (audioDriver) audioDriver.prepare().catch(function (failure) {
        if (!disposed && failure.name !== 'AbortError') setStatus("讲解声音未能启动，请再点一次播放");
      });
    }

    function playVideo() {
      if (video && video.play) {
        var played = audioDriver ? audioDriver.play() : video.play();
        if (played && played.catch) played.catch(function (failure) {
          if (!disposed && (!failure || failure.name !== 'AbortError')) setStatus("浏览器未开始播放，请再点一次播放");
        });
      }
    }

    function playStep(index, resume, metadataReady, autoplay) {
      if (disposed || !video || !Number.isInteger(index) || !steps[index]) return false;
      autoplay = autoplay !== false;
      if (autoplay) prepareAudio();
      pendingStep = -1;
      pendingAutoplay = autoplay;
      continuous = false;
      if (playAllButton) playAllButton.setAttribute("aria-pressed", "false");
      cancelFrame();
      var previous = activeStep;
      var completed = stepComplete;
      activeStep = index;
      stepComplete = false;
      highlight(index, true);
      var bounds = stepBounds(index);
      // Declared step times do not imply that the media can seek yet. Chrome
      // may expose an early currentTime assignment, then reset it during load.
      if(!metadataReady&&video.readyState<1&&number(video.duration,null)===null){
        pauseVideo();activeStep=-1;pendingStep=index;
        setPlayLabel(autoplay?'暂停':'播放这一步');video.preload='metadata';
        setStatus('正在读取本步的时间范围…');try{video.load();}catch(err){}
        return true;
      }
      if (!bounds) {
        pauseVideo();
        activeStep = -1;
        if (!metadataReady && number(video.duration, null) === null) {
          pendingStep = index;
          setPlayLabel(autoplay ? "暂停" : "播放这一步");
          video.preload = "metadata";
          setStatus("正在读取本步的时间范围…");
          try { video.load(); } catch (err) { /* 仍可使用连续播放 */ }
        } else setStatus("本步时间范围不可用，可以选择连续播放查看整段动画");
        return true;
      }
      if (!resume || previous !== index || completed || video.currentTime < bounds.start || video.currentTime >= bounds.end) {
        if (!video.paused) pauseVideo();
        seek(bounds.start);
      }
      if (autoplay) { playVideo(); scheduleFrame(); }
      else pauseVideo();
      return true;
    }

    function stepFromEvent(event) {
      var node = event.target && event.target.closest
        ? event.target.closest('[data-action="step"]') : null;
      if (!node || !player.contains(node)) return;
      playStep(Number(node.getAttribute("data-step")), false);
    }

    function playAll() {
      if (!video) return;
      pendingStep = -1;
      activeStep = -1;
      stepComplete = false;
      continuous = true;
      if (playAllButton) playAllButton.setAttribute("aria-pressed", "true");
      cancelFrame();
      highlight(current, true);
      if (video.ended || video.currentTime >= number(video.duration, Infinity)) seek(0);
      playVideo();
    }
    on(playButton, "click", function () {
      if (!video) return;
      if (!video.paused || pendingStep >= 0 && pendingAutoplay) {
        pendingStep = -1;
        pauseVideo();
        // Cancelling a metadata wait may pause an already-paused video, which
        // does not emit a pause event in browsers.
        updatePausedState();
      }
      else if (continuous) playAll();
      else if (steps.length) playStep(Math.max(0, current), true);
      else playVideo();
    });
    on(replayButton, "click", function () {
      if (steps.length) playStep(Math.max(0,current), false);
      else if (video) { seek(0); playVideo(); }
    });
    on(nextButton, "click", function () { playStep(current + 1, false); });
    on(playAllButton, "click", playAll);
    on(fullscreenButton, "click", function () {
      if(options.compact){var expanded=player.classList.toggle('animation-expanded');fullscreenButton.textContent=expanded?'收起':'放大';fullscreenButton.setAttribute('aria-label',expanded?'收起动画':'放大动画');fullscreenButton.setAttribute('aria-pressed',String(expanded));return;}
      try {
        var attempt;
        if (doc.fullscreenElement === player && doc.exitFullscreen) attempt = doc.exitFullscreen();
        else if (player.requestFullscreen) attempt = player.requestFullscreen();
        else if (video && video.webkitEnterFullscreen) video.webkitEnterFullscreen();
        else setStatus("此浏览器不支持全屏，可继续在页面中播放。");
        if (attempt && attempt.catch) attempt.catch(function () {
          if (!disposed) setStatus("全屏未打开，可以继续在页面中播放。");
        });
      } catch (err) { if (!disposed) setStatus("全屏未打开，可以继续在页面中播放。"); }
    });
    on(doc, "fullscreenchange", function () {
      if(options.compact)return;
      var fullscreen = doc.fullscreenElement === player;
      fullscreenButton.setAttribute("aria-label", fullscreen ? "退出全屏" : "全屏查看动画");
      fullscreenButton.setAttribute("aria-pressed", fullscreen ? "true" : "false");
    });
    if(options.compact){fullscreenButton.textContent='放大';fullscreenButton.setAttribute('aria-label','放大动画');on(player,'keydown',function(e){if(e.key==='Escape'&&player.classList.contains('animation-expanded')){fullscreenButton.click();fullscreenButton.focus();}});}
    on(speed, "change", function () {
      if (video) video.playbackRate = number(speed.value, 1);
    });
    var externalSpeedOption = null;
    function syncSpeed() {
      if (!video) return;
      var value = String(number(video.playbackRate, 1));
      if (externalSpeedOption && externalSpeedOption.value !== value) {
        externalSpeedOption.remove(); externalSpeedOption = null;
      }
      if (!Array.prototype.some.call(speed.options, function (option) { return option.value === value; })) {
        externalSpeedOption = element(doc, "option", {"value": value}, value + "×");
        speed.appendChild(externalSpeedOption);
      }
      speed.value = value;
    }
    on(video, "ratechange", syncSpeed);
    syncSpeed();
    function syncVolume() {
      if (!video) return;
      volume.value = String(video.volume);
      muteButton.setAttribute("aria-pressed", video.muted ? "true" : "false");
      muteButton.setAttribute("aria-label", video.muted ? "开启讲解声音" : "静音讲解");
      muteButton.textContent = video.muted ? "开启声音" : "静音";
    }
    on(volume, "input", function () {
      if (video) { video.volume = Math.max(0, Math.min(1, number(volume.value,1))); syncVolume(); }
    });
    on(muteButton, "click", function () { if (video) { video.muted = !video.muted; syncVolume(); } });
    on(video, "volumechange", syncVolume);
    function captionIndexAt(time) {
      for (var index = steps.length - 1; index >= 0; index -= 1) {
        var bounds = stepBounds(index);
        var start = bounds ? bounds.start : steps[index].start;
        var end = bounds ? bounds.end : Infinity;
        if (time >= start && time < end) return index;
        if (time >= start) return -1;
      }
      return -1;
    }
    function showCaption() {
      if (!captionLayer) return;
      var index = video && captionsOn && playedOnce ? captionIndexAt(number(video.currentTime, 0)) : -1;
      if (index === captionShown) return;
      captionShown = index;
      if (index < 0) { captionLayer.setAttribute("hidden", ""); captionLayer.textContent = ""; return; }
      captionText(captionLayer, steps[index].caption);
      captionLayer.setAttribute("data-step", String(index));
      captionLayer.removeAttribute("hidden");
    }
    function nativeTrackMode() {
      var track = trackState(video);
      if (track) { try { track.mode = captionsOn ? "hidden" : "disabled"; } catch (err) { /* 不支持的实现 */ } }
    }
    on(captionsButton, "click", function () {
      if (!video) return;
      captionsOn = !captionsOn;
      nativeTrackMode();
      captionShown = -2;
      showCaption();
      captionsButton.setAttribute("aria-pressed", captionsOn ? "true" : "false");
    });
    on(transcriptButton, "click", function () {
      if (!transcriptHost) return;
      /* 缺件时文字稿是**兜底**：不许被关掉（关掉就等于静默消失）。 */
      if (broken && transcriptShown) { setStatus("缺件时文字稿保持展开"); return; }
      transcriptShown = !transcriptShown;
      if (transcriptShown) transcriptHost.removeAttribute("hidden");
      else transcriptHost.setAttribute("hidden", "");
      transcriptButton.setAttribute("aria-expanded", transcriptShown ? "true" : "false");
    });
    on(player, "click", stepFromEvent);
    if (posterButton) on(posterButton, "click", function () { playButton.click(); });

    if (video) {
      on(video, "loadedmetadata", function () {
        nativeTrackMode();
        captionShown = -2;
        showCaption();
        if (pendingStep >= 0) playStep(pendingStep, false, true, pendingAutoplay);
        else if (activeStep >= 0) {
          var bounds = stepBounds(activeStep);
          if (!bounds) {
            pauseVideo(); activeStep = -1;
            setStatus("本步时间范围超出视频，可以选择连续播放查看整段动画");
          } else if (video.currentTime < bounds.start) seek(bounds.start);
          else stopAtBoundary();
        }
      });
      on(video, "timeupdate", function () {
        showCaption();
        if (activeStep >= 0) { stopAtBoundary(); return; }
        var now = number(video.currentTime, 0);
        for (var index = steps.length - 1; index >= 0; index -= 1) {
          if (now >= steps[index].start) { highlight(index); break; }
        }
      });
      on(video, "play", function () {
        playedOnce = true;
        if(steps[current])captionText(stageTitle,steps[current].title);
        if (posterButton) posterButton.hidden = true;
        showCaption();
        player.setAttribute("data-playing", "true");
        setPlayLabel("暂停");
        playbackHint();
        scheduleFrame();
      });
      on(video, "pause", updatePausedState);
      on(video, "seeked", showCaption);
      on(video, "ended", function () {
        cancelFrame();
        player.removeAttribute("data-playing");
        if (activeStep >= 0) { stepComplete = true; setPlayLabel("重播这一步"); }
        else setPlayLabel("重播");
        playbackHint();
      });
      on(video, "error", function () {
        cancelFrame();
        pendingStep = -1;
        /* 文件在、但解不了／取不到：也要看得见，并把文字稿顶上来 */
        player.classList.add("has-error");
        broken = true;
        transcriptShown = true;
        if (transcriptHost) transcriptHost.removeAttribute("hidden");
        transcriptButton.setAttribute("aria-expanded", "true");
        if (!error) {
          error = element(doc, "p", {"class": "animation-error", "data-animation-error": "",
                                     "role": "alert"},
                          "视频没能加载（文件缺失、损坏或不被允许）：下面是同一份文字稿。");
          details.insertBefore(error, status);
        }
        setStatus("视频加载失败");
      });
    }
    root.appendChild(player);
    if (steps.length) highlight(0);
    nativeTrackMode();
    showCaption();

    function dispose() {
      if (disposed) return;
      disposed = true;
      pendingStep = -1;
      cancelFrame();
      if (audioDriver) { audioDriver.dispose(); audioDriver = null; }
      if (video) {
        try { pauseVideo(); } catch (err) { /* 未知媒体实现 */ }
        var track = video.querySelector ? video.querySelector("track") : null;
        if (blobCaptions) { try { URL.revokeObjectURL(blobCaptions); } catch (err) { /* 已回收 */ } }
        /* 先摘 track 的 src 再摘 video 的：留着 src 会让浏览器继续取资源 */
        if (track) { track.removeAttribute("src"); if (track.parentNode) track.parentNode.removeChild(track); }
        video.removeAttribute("src");
        video.removeAttribute("poster");
        try { video.load(); } catch (err) { /* 不支持的实现 */ }
      }
      listeners.forEach(function (row) {
        try { row[0].removeEventListener(row[1], row[2]); } catch (err) { /* 已摘掉 */ }
      });
      listeners = [];
      if (player.parentNode) player.parentNode.removeChild(player);
      current = -1;
    }
    dispose.player = player;
    dispose.selectStep = function (index, options) {
      return playStep(index, false, false, !options || options.autoplay !== false);
    };
    return dispose;
  }

  return {
    mountAnimations: mountAnimations,
    resourceUrl: resourceUrl,
    captionsSource: captionsSource,
    webvttFromSteps: webvttFromSteps,
    captionText: captionText,
    isOpaqueOrigin: isOpaqueOrigin,
    stepsOf: stepsOf,
    missingKinds: missingKinds,
    REQUIRED: REQUIRED,
    ATTRS: ATTRS,
  };
});
