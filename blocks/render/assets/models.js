/* 交互模型运行时（本地、离线）：把 `models[]` 规格变成能上手的东西。
 *
 * 分工（见《AIforStudy-模型规格规范.md》）：
 *   * 教案（或 API 里的模型）只给**数据**：模板、参数、步骤、说明；
 *   * 这里负责**怎么画**：相机、尺寸、坐标范围、网格分格、层级、颜色、控件长相。
 * 所以模型永远不会写 JS/WebGL，也就不会写出"时而能跑时而空白"的东西（G2）。
 *
 * 三类内置渲染模板：
 *   * canvas   → MathBox（几何/空间：网格、向量、平面、变换）
 *   * symbolic → KaTeX + 分步按钮（矩阵/方程组这类"排列"，本质是排版）
 *   * svg      → 固定解析式的等值线交互（无脚本规格、无额外依赖）
 *
 * 这里写死的几条，都是实测踩出来的（规范第五节）：
 *   * 坐标范围**必须正方**（不然正方形会被拉成瘦长条）；
 *   * 相机距离按画布高度算（每单位像素 ≈ 444 ÷ 距离，fov 60）；
 *   * 面片一律压 z（zBias −3），线提 z（+6）—— 同平面会打架；
 *   * 两点线段用 `vector` 关箭头（两点的 `line` 在这版 MathBox 里画不出来）；
 *   * 公式不进画布。
 */
(function (global) {
  "use strict";

  var COLORS = {
    grid: "#e2e8f0", axis: "#475569", primary: "#2563eb", secondary: "#f59e0b",
    emphasized: "#16a34a", warn: "#dc2626", ink: "#1f2430"
  };
  /* 每单位像素 = K / 相机距离（fov 60，实测 222 px/单位 @ 距离 2.0） */
  var PX_PER_UNIT_AT_1 = 444;     /* 参考值，见下面 CAMERA_K 的说明 */
  var CAMERA_K = 1240;            /* 相机距离 = 1240 ÷ 画布高度 → range 占画布高度约 86% */
  var TARGET_FILL = 0.86;         /* 目标占比（取值与 CAMERA_K 配套） */

  var mathboxPromise = null;

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src; s.onload = resolve;
      s.onerror = function () { reject(new Error("加载失败：" + src)); };
      document.head.appendChild(s);
    });
  }

  /* ── P-042：读数里的公式走 KaTeX，不许用字符串拼 ──────────────────────────
     用户 2026-09-25 的审查里点了这一条（"模型读数那一行排版有问题"）：原来是
     `coefs[i] + "v" + (i+1)` 这样拼出来的，页面上就是"0.8v1+1.5v2 = (2.30, 1.10)"——
     符号、数字、下标全挤在一行里，跟正文的公式两种样子（正文是 KaTeX 排的）。
     现在统一走**行内 KaTeX**；KaTeX 万一没加载就退回等宽文本（绝不白屏）。 */
  function texInline(tex) {
    try {
      if (!global.katex) throw new Error("这一页没有加载 KaTeX");
      return global.katex.renderToString(tex, { displayMode: false, throwOnError: false,
                                                strict: false });
    } catch (e) { return "<code>" + esc(tex) + "</code>"; }
  }

  /** 按需把 MathBox 拉进来（只有页面里真有 canvas 模型时才拉，1.1MB 不白给）。 */
  function ensureMathBox() {
    if (global.MathBox && global.MathBox.mathBox) return Promise.resolve();
    if (mathboxPromise) return mathboxPromise;
    /* 默认从**页面同级的** assets/mathbox/ 加载；冒烟页/测试页可以用
       `window.__MATHBOX_BASE__` 指到别处（模板冒烟页就是这么验的）。 */
    var base = global.__MATHBOX_BASE__ || "assets/mathbox/";
    // New readers preload the same Three runtime before models/app. Reuse its
    // identity so existing scenes and MathBox cannot race two THREE instances.
    mathboxPromise = (global.THREE ? Promise.resolve() : loadScript(base + "three.min.js"))
      .then(function () { return loadScript(base + "OrbitControls.js"); })
      .then(function () { return loadScript(base + "mathbox.min.js"); })
      .then(function () {
        if (!(global.MathBox && global.MathBox.mathBox)) throw new Error("MathBox 没就绪");
      });
    return mathboxPromise;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function num(v, dflt) {
    var n = typeof v === "number" ? v : parseFloat(v);
    return isFinite(n) ? n : dflt;
  }
  function clampNum(v, lo, hi, dflt) { return Math.min(hi, Math.max(lo, num(v, dflt))); }

  /* ── 控件（长相由运行时定，模型只说要什么） ───────────────────────── */
  function sliderRow(label, value, range, step, onInput) {
    var row = document.createElement("label");
    row.className = "mdl-slider";
    var name = document.createElement("span");
    name.className = "mdl-slider-name";
    name.textContent = label;
    var input = document.createElement("input");
    input.type = "range";
    input.min = range[0]; input.max = range[1]; input.step = step;
    input.value = value;
    var out = document.createElement("span");
    out.className = "mdl-slider-value";
    out.textContent = (+value).toFixed(2);
    input.addEventListener("input", function () {
      out.textContent = (+input.value).toFixed(2);
      onInput(parseFloat(input.value));
    });
    row.appendChild(name); row.appendChild(input); row.appendChild(out);
    return { el: row, input: input, out: out };
  }

  function button(label, cls, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "mdl-btn" + (cls ? " " + cls : "");
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  /* ── 坐标范围：按内容包络算，强制正方（规范：这条只能渲染层做） ────── */
  function squareRange(points) {
    var max = 1;
    (points || []).forEach(function (p) {
      (p || []).forEach(function (v) { max = Math.max(max, Math.abs(num(v, 0))); });
    });
    var half = Math.min(6, Math.ceil(max * 1.15 * 10) / 10);
    return { half: half, range: [[-half, half], [-half, half], [-1, 1]] };
  }

  /* 网格：铺满画布的宽高（rangeX/rangeY），但**格子保持方的**（divideX/divideY 按比例）。 */
  function gridSpec(canvasW, canvasH, half) {
    var aspect = canvasW / Math.max(1, canvasH);
    var rY = half * 1.02, rX = rY * aspect;
    var cell = half / 3;                     /* 半轴 3 格左右，跟纸上网格一个密度 */
    return {
      lineX: true, lineY: true,
      rangeX: [-rX, rX], rangeY: [-rY, rY],
      divideX: Math.max(4, Math.round((2 * rX) / cell)),
      divideY: Math.max(4, Math.round((2 * rY) / cell))
    };
  }

  /* ── canvas：MathBox 底座（相机/尺寸/底色/取景都由这里定） ─────────── */
  function bootCanvas(stage, half, opts) {
    /* opts.view = [方位角, 仰角]（度数）。P-003 用户原话「那个三维的向量实在不好」——
       病根之一是**没有"该从哪个角度看"的引导**。三维模板因此必须给 view，
       相机的**距离**仍然只由画布高度决定（见下面 dist 的说明），view 只改方向。 */
    var look = (opts && Array.isArray(opts.view) && opts.view.length === 2)
      ? [num(opts.view[0], 40), num(opts.view[1], 25)] : null;
    var rect = stage.getBoundingClientRect();
    var w = Math.max(240, Math.round(rect.width || stage.clientWidth || 640));
    var h = Math.max(200, Math.round(w * (stage.dataset.aspect ? parseFloat(stage.dataset.aspect) : 0.62)));
    stage.style.height = h + "px";
    /* ⚠️ 实测：`mathBox()` 有时返回的是**还没就绪的 Bootstrap**（不是那棵树）。
       原因不明（同样代码在探针页 10 次都对、在这一页偶尔不对），所以这里**重试**：
       每次清空容器重建，最多 3 次；3 次都不行就交给上层显示兜底说明（不降级、不画第二套）。 */
    function attempt() {
      return new Promise(function (resolve, reject) {
        var mb = global.MathBox.mathBox({
          plugins: ["core", "controls", "cursor"],
          element: stage,
          size: { width: w, height: h },
          controls: { klass: global.THREE.OrbitControls,
                      parameters: { enableRotate: !!stage.dataset.rotate } },
          mathbox: { inspect: false, splash: false }
        });
        /* 用 rAF 轮询而不是 setTimeout：MathBox 自己也是靠帧循环起来的，
           跟着帧走才不会在"虚拟时间被快进"的测试环境里被抢跑（实测：setTimeout 会抢跑）。 */
        var n = 0;
        (function wait() {
          var api = mb && (typeof mb.cartesian === "function" ? mb : mb.mathbox);
          if (api && api.three && api.three.renderer) return resolve(api);
          if (++n > 300) return reject(new Error("这次没起来"));
          (global.requestAnimationFrame || function (f) { setTimeout(f, 16); })(wait);
        })();
      });
    }
    function retry() {
      return function () {
        try { if (stage.firstChild) stage.innerHTML = ""; } catch (e) {}
        return new Promise(function (r) { setTimeout(r, 150); }).then(attempt);
      };
    }
    return attempt()
      .catch(retry())
      .catch(retry())
      .then(function (api) {
    var three = api.three;
    try { three.renderer.setClearColor(new global.THREE.Color("#ffffff"), 1); } catch (e) {}
    /* 相机距离 = 1240 ÷ 画布高度（**与 range 无关**）。
       ⚠️ 这里我第一版写错了：写成"距离随 half 变"，结果 range 一大就把整张图缩成一小团
       （实测：half=7 时相机被推到 17，图上只剩一个点）。规律是
       **每单位像素 = 533 ÷ (距离 × range半宽)**，所以距离只跟画布高度有关。 */
    var dist = CAMERA_K / h;
    var cam = stage.dataset.camera ? stage.dataset.camera.split(",").map(Number) : null;
    var pos = cam || (look
      ? [dist * Math.cos(look[1] * Math.PI / 180) * Math.cos(look[0] * Math.PI / 180),
         dist * Math.sin(look[1] * Math.PI / 180),
         dist * Math.cos(look[1] * Math.PI / 180) * Math.sin(look[0] * Math.PI / 180)]
      : [dist * 0.45, dist * 0.35, dist * 0.82]);
    function reset(newPos) {
      if (newPos) pos = newPos;
      three.camera.position.set(pos[0], pos[1], pos[2]);
      three.camera.lookAt(new global.THREE.Vector3(0, 0, 0));
      if (three.controls) { three.controls.target.set(0, 0, 0); three.controls.update(); }
    }
    reset();
    stage.__diag = { w: w, h: h, half: half, dist: Math.round(dist * 100) / 100,
                     view: look ? look.slice() : null };
    /* ── P-025-1：把"怎么销毁这个画布"登记在元素上 ──────────────────────────
       为什么必须销毁：浏览器对 WebGL 上下文有数量上限，换节时只把 DOM 换掉的话，
       Three.js 的上下文、渲染循环、GPU 资源全都留着 —— 读者多点几节，
       后面的模型就初始化不出来（表现就是"一直显示正在加载…"）。 */
    stage.__disposeModel = function () {
      var id = stage.dataset.modelId;
      try { if (api && typeof api.destroy === "function") api.destroy(); } catch (e) {}
      try { if (three && typeof three.destroy === "function") three.destroy(); } catch (e) {}
      try { if (three && three.renderer) three.renderer.dispose(); } catch (e) {}
      try {                                   // 真正把 GPU 上下文还回去（dispose 只是释放资源）
        if (three && three.renderer && three.renderer.forceContextLoss) {
          three.renderer.forceContextLoss();
        }
      } catch (e) {}
      try { if (three && three.renderer && three.renderer.domElement &&
                 three.renderer.domElement.parentNode) {
        three.renderer.domElement.parentNode.removeChild(three.renderer.domElement);
      } } catch (e) {}
      try { if (window.__MODEL_RENDERERS__) delete window.__MODEL_RENDERERS__[id]; } catch (e) {}
      stage.__diag = null;
    };
    /* 登记一下，供"[模型] … 绘制调用=" 那行自报用（无头渲染时我直接读 console） */
    window.__MODEL_RENDERERS__ = window.__MODEL_RENDERERS__ || {};
    setTimeout(function () {
      try {
        var info = three.renderer.info.render;
        window.__MODEL_RENDERERS__[stage.dataset.modelId || "?"] = {
          calls: info.calls, triangles: info.triangles, w: w, h: h
        };
      } catch (e) {}
    }, 900);
    return { mb: api, three: three, w: w, h: h, reset: reset };
    });
  }

  /* ── 模板：行变换（canvas） ────────────────────────────────────────
     P-003：用户原话「哪有矩阵只要改一个地方的」。旧模板让人拖 4 个滑块改矩阵里的
     **一个数字** —— 可教材里矩阵的动作从来不是这个，而是**整行一起动**：
       交换两行 / 某一行乘一个非零常数 / 某行的倍数加到另一行。
     所以这里：
       * 控件是"选一种行操作 + 拖系数 λ + 选改哪一行"，一次改**一整行**；
       * 左边把矩阵写成文字行（2~3 列，能装下增广矩阵），右边平面画出**行向量**；
       * 两者**同时**变 —— 这就是"整行一起动"看得见的样子；
       * 读数报 det：λ=0 时整行变零、det=0、矩阵不可逆（正好接上批注"乘的常数不能是零"）。 */
  function renderRowOperation(stage, ui, spec) {
    var params = spec.params || {};
    var base = (params.matrix && params.matrix.length === 2 ? params.matrix : [[1, 0], [0, 1]])
      .map(function (row) { return row.map(function (v) { return num(v, 0); }); });
    var cols = base[0].length;
    var state = {
      op: params.op === "swap" || params.op === "scale" || params.op === "addMultiple"
        ? params.op : "addMultiple",
      lambda: num(params.lambda, 0),
      rows: Array.isArray(params.rows) && params.rows.length === 2
        ? [clampNum(params.rows[0], 1, 2, 2), clampNum(params.rows[1], 1, 2, 1)] : [2, 1]
    };
    var show = params.show || ["grid", "rows", "rowsAfter"];
    var sliderRange = [-3, 3], step = 0.1;
    (spec.controls || []).forEach(function (c) {
      if (Array.isArray(c.range) && c.range.length === 2) {
        sliderRange = [num(c.range[0], -3), num(c.range[1], 3)];
      }
      if (c.step) step = num(c.step, 0.1);
    });

    /** 把一个行操作作用到矩阵上（返回新矩阵，不改原矩阵）。 */
    function applyOp(matrix, op, lambda, rows) {
      var i = Math.min(matrix.length - 1, Math.max(0, Math.round(rows[0]) - 1));
      var j = Math.min(matrix.length - 1, Math.max(0, Math.round(rows[1]) - 1));
      var out = matrix.map(function (r) { return r.slice(); });
      if (op === "swap") { var tmp = out[i]; out[i] = out[j]; out[j] = tmp; }
      else if (op === "scale") { out[i] = out[i].map(function (v) { return v * lambda; }); }
      else { out[i] = out[i].map(function (v, k) { return v + lambda * out[j][k]; }); }
      return out;
    }
    function detOf(m) {
      /* 增广矩阵只拿前两列算行列式（第三列是常数项，不参与"面积"） */
      return m[0][0] * m[1][1] - m[0][1] * m[1][0];
    }
    function rowTex(m) {
      return m.map(function (row, i) {
        return "第" + (i + 1) + "行　[" + row.map(function (v) {
          return (Math.abs(v) < 1e-9 ? 0 : v).toFixed ? (+v).toFixed(2).replace(/\.00$/, "") : v;
        }).join("　") + "]";
      }).join("\n");
    }
    /* 每一步 = 一个行操作；从原矩阵**累积**重放（教材的"下一步"就是这么走的） */
    var steps = (spec.steps || []).filter(function (s) { return s && s.state; });
    function matrixAt(n) {
      var m = base.map(function (r) { return r.slice(); });
      var hint = { op: state.op, lambda: 0, rows: state.rows.slice() };
      for (var k = 0; k <= n && k < steps.length; k++) {
        var st = steps[k].state || {};
        if (st.matrix) { m = st.matrix.map(function (r) { return r.slice(); }); continue; }
        hint = { op: st.op || hint.op, lambda: num(st.lambda, 0), rows: st.rows || hint.rows };
        m = applyOp(m, hint.op, hint.lambda, hint.rows);
      }
      return m;
    }

    var half = 3.1;
    return bootCanvas(stage, half).then(function (g) {
      var view = g.mb.cartesian({ range: [[-half, half], [-half, half], [-1, 1]], scale: [1, 1, 1] });
      var ref = view.group();
      if (show.indexOf("grid") >= 0) ref.grid(gridSpec(g.w, g.h, half));
      view.axis({ axis: 1, color: COLORS.axis, width: 2 });
      view.axis({ axis: 2, color: COLORS.axis, width: 2 });
      /* 原来那两行（灰，作对照）+ 变化后的两行（彩色） */
      var baseLines = [], nowLines = [], nowPara = null;
      [0, 1].forEach(function (i) {
        var color = i === 0 ? COLORS.primary : COLORS.secondary;
        baseLines.push(ref.array({ data: [[0, 0, 0], [base[i][0], base[i][1], 0]], channels: 3, items: 2 })
          .vector({ width: 3, color: "#cbd5e1", start: false, end: true, zBias: 5 }));
        /* ⚠️ 要留住 **array 那个节点**：`.vector(...)` 返回的是 vector 选择，
           `vector.set("data", …)` 会报 "Setting unknown property `data`" 而且**箭头不会动**
           （实测控制台里那一串 `<vector#10 /> - Setting unknown property data=…` 就是它，
           表现是"拖动滑块时两条彩色行向量不跟着变"）。*/
        var rowArray = view.array({ data: [[0, 0, 0], [base[i][0], base[i][1], 0]],
                                    channels: 3, items: 2 });
        rowArray.vector({ width: 7, color: color, start: false, end: true, zBias: 9 });
        nowLines.push(rowArray);
      });
      /* 两行张成的平行四边形：面积 = |det|（行向量版） */
      nowPara = view.transform({ matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] });
      nowPara.array({ data: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], channels: 3, items: 4 })
        .face({ color: COLORS.emphasized, opacity: 0.22, zBias: -3 });

      /* 文字矩阵：和箭头**同时**更新，这一条就是"整行一起动" */
      var text = document.createElement("div");
      text.className = "mdl-matrix-rows";
      var readout = document.createElement("div");
      readout.className = "mdl-readout";

      function paint(m) {
        text.textContent = rowTex(m);
        nowLines.forEach(function (rowArray, i) {
          rowArray.set("data", [[0, 0, 0], [m[i][0], m[i][1], 0]]);
        });
        /* 列主序 16 个数：第 1 列 = 第 1 行向量，第 2 列 = 第 2 行向量 */
        nowPara.set("matrix", [m[0][0], m[0][1], 0, 0, m[1][0], m[1][1], 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
        var det = detOf(m);
        var why = "";
        if (state.op === "scale" && Math.abs(state.lambda) < 1e-9) why = "　λ=0：整行变成零行 → det=0，矩阵不可逆";
        else if (Math.abs(det) < 1e-9) why = "　det=0：两行共线，平面被压成一条线";
        readout.innerHTML = texInline("\\det = " + det.toFixed(2)) +
          (why ? esc(why) : "");
      }

      /* ── 控件：三种行操作（改的是整行）── */
      var OP_LABEL = { swap: "交换两行", scale: "整行乘 λ", addMultiple: "倍加 rᵢ+λrⱼ" };
      var opBtns = {};
      var opBox = document.createElement("div");
      opBox.className = "mdl-steps";
      ["swap", "scale", "addMultiple"].forEach(function (name) {
        var btn = button(OP_LABEL[name], name === state.op ? "primary" : "", function () {
          state.op = name;
          Object.keys(opBtns).forEach(function (k) { opBtns[k].className = k === name ? "mdl-btn primary" : "mdl-btn"; });
          syncAndPaint();
        });
        opBtns[name] = btn;
        opBox.appendChild(btn);
      });
      var rowBtn = button("改第 " + state.rows[0] + " 行", "", function () {
        state.rows = [state.rows[0] === 1 ? 2 : 1, state.rows[1]];
        rowBtn.textContent = "改第 " + state.rows[0] + " 行";
        syncAndPaint();
      });
      var lamRow = sliderRow("λ", state.lambda, sliderRange, step, function (v) {
        state.lambda = v; syncAndPaint();
      });
      var resetBtn = button("回到原矩阵", "", function () { state.lambda = 0; lamRow.input.value = 0; lamRow.out.textContent = "0.00"; syncAndPaint(); });
      var bar = document.createElement("div");
      bar.className = "mdl-steps";
      bar.appendChild(rowBtn); bar.appendChild(resetBtn);
      ui.appendChild(opBox);
      ui.appendChild(lamRow.el);
      ui.appendChild(bar);
      ui.appendChild(text);
      ui.appendChild(readout);
      ui.appendChild(button("重置视角", "", g.reset));

      function syncAndPaint() { paint(applyOp(base, state.op, state.lambda, state.rows)); }

      /* 分步：每一步就是一个行操作（有 steps 才加这排按钮） */
      if (steps.length >= 2) {
        var idx = 0;
        var label = document.createElement("span");
        label.className = "mdl-step-label";
        var bar2 = document.createElement("div");
        bar2.className = "mdl-steps";
        function go(n) {
          idx = Math.max(0, Math.min(steps.length - 1, n));
          var st = steps[idx].state || {};
          if (st.op) state.op = st.op;
          if (st.rows) state.rows = st.rows.slice();
          if (st.lambda !== undefined) { state.lambda = num(st.lambda, 0); lamRow.input.value = state.lambda; }
          if (st.op) text.dataset.op = st.op;
          var m = matrixAt(idx);
          paint(m);
          label.textContent = "第 " + (idx + 1) + " / " + steps.length + " 步：" + (steps[idx].label || "") +
            (st.op && st.op !== "swap" && st.lambda !== undefined ? "　（" + OP_LABEL[st.op] + "，λ=" + state.lambda + "）" : "");
        }
        bar2.appendChild(button("上一步", "", function () { go(idx - 1); }));
        bar2.appendChild(button("下一步 ▶", "primary", function () { go(idx + 1); }));
        bar2.appendChild(button("回到开始", "", function () { go(0); }));
        bar2.appendChild(label);
        ui.appendChild(bar2);
        go(0);
      } else {
        syncAndPaint();
      }
    });
  }

  /* ── 模板：线性组合（canvas） ───────────────────────────────────────
     看到什么：c₁v₁ + c₂v₂ 在平面上扫出的所有点；系数拖到哪、点跑到哪；
     两个向量共线时，怎么拖都铺不满平面。 */
  function renderVectorCombination(stage, ui, spec) {
    var params = spec.params || {};
    var vecs = (params.vectors || [[1, 0]]).map(function (v) {
      return [num(v && v[0], 1), num(v && v[1], 0)];
    }).slice(0, 3);
    var coefs = (params.coefficients || vecs.map(function () { return 1; }))
      .map(function (c) { return c; }).slice(0, vecs.length);
    while (coefs.length < vecs.length) coefs.push(1);
    coefs = coefs.map(function (c) { return clampNum(c, -3, 3, 1); });   /* 和滑块范围对齐 */
    var show = params.show || ["grid", "vectors", "sum"];
    var showSum = show.indexOf("sum") >= 0;
    var showPara = show.indexOf("parallelogram") >= 0 && vecs.length >= 2;
    var sliderLo = -3, sliderHi = 3, step = 0.1;   /* 规范允许到 ±4；给 ±3 是为了让"消去首分量"这种例子够用 */
    (spec.controls || []).forEach(function (c) {
      if (Array.isArray(c.range) && c.range.length === 2) {
        sliderLo = clampNum(c.range[0], -4, 0, -2); sliderHi = clampNum(c.range[1], 0, 4, 2);
      }
      if (c.step) step = clampNum(c.step, 0.01, 1, 0.1);
    });
    /* 取景：按**当前内容**算（和向量最容易跑出画面 —— 实测第一版就是这样，绿箭头根本看不见），
       再乘 1.25 留边；上限 7 是为了"系数一拖就变得很大"时不至于把图缩成一个点。 */
    function reachOf() {
      var sx = 0, sy = 0, longest = 1;
      vecs.forEach(function (v, i) {
        sx += coefs[i] * v[0]; sy += coefs[i] * v[1];
        longest = Math.max(longest, Math.hypot(v[0], v[1]),
                           Math.hypot(coefs[i] * v[0], coefs[i] * v[1]));
      });
      return Math.max(Math.hypot(sx, sy), longest);
    }
    function halfOf() { return Math.min(7, Math.max(1.2, Math.ceil(reachOf() * 1.25 * 10) / 10)); }
    var half = halfOf();

    return bootCanvas(stage, half).then(function (g) {
      var view = g.mb.cartesian({ range: [[-half, half], [-half, half], [-1, 1]], scale: [1, 1, 1] });
      if (show.indexOf("grid") >= 0) grid0 = view.grid(gridSpec(g.w, g.h, half));
      view.axis({ axis: 1, color: COLORS.axis, width: 2 });
      view.axis({ axis: 2, color: COLORS.axis, width: 2 });

      var VCOLORS = [COLORS.primary, COLORS.secondary, "#7c3aed"];
      vecs.forEach(function (v, i) {
        view.array({ data: [[0, 0, 0], [v[0], v[1], 0]], channels: 3, items: 2 })
          .vector({ width: 6, color: VCOLORS[i % VCOLORS.length], start: false, end: true, zBias: 8 });
      });

      function sumVec() {
        var x = 0, y = 0;
        vecs.forEach(function (v, i) { x += coefs[i] * v[0]; y += coefs[i] * v[1]; });
        return [x, y];
      }
      var Tsum = null, para = null;
      if (showSum) {
        /* 和向量：单位向量 (1,0) 经 transform 缩放到 (sumX, sumY) —— 比改 data 稳（实测有效） */
        Tsum = view.transform({ matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] });
        Tsum.array({ data: [[0, 0, 0], [1, 0, 0]], channels: 3, items: 2 })
          .vector({ width: 9, color: COLORS.emphasized, start: false, end: true, zBias: 10 });
      }
      if (showPara) {
        /* 平行四边形 = 单位正方形被矩阵 [[c₁v₁x, c₂v₂x], [c₁v₁y, c₂v₂y]] 拉过去的样子。
           为什么这么做：`face` 图元**不接受 `data`**（实测报 unknown property），
           但 `transform` 的 `matrix` 能改 —— 于是把四边形写成"单位正方形 + 矩阵"，
           系数一变就只改矩阵（和 matrix-transform 用的是同一条路）。 */
        para = view.transform({ matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] });
        para.array({ data: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], channels: 3, items: 4 })
          .face({ color: COLORS.emphasized, opacity: 0.22, zBias: -3 });
      }
      var readout = document.createElement("div");
      readout.className = "mdl-readout";
      ui.appendChild(readout);

      var curHalf = half, grid0 = null;
      function reframeIfNeeded() {
        var need = halfOf();
        if (need <= curHalf * 0.98) return;          /* 只放大，不缩小 —— 免得拖滑块时画面忽大忽小 */
        curHalf = need;
        try {
          view.set("range", [[-need, need], [-need, need], [-1, 1]]);
          if (grid0) {
            var sp = gridSpec(g.w, g.h, need);
            grid0.set("rangeX", sp.rangeX); grid0.set("rangeY", sp.rangeY);
            grid0.set("divideX", sp.divideX); grid0.set("divideY", sp.divideY);
          }
          /* 相机**不动**：距离只跟画布高度有关（见 bootCanvas 的说明） */

        } catch (e) { if (global.console) console.warn("[模型] 重取景失败：" + e.message); }
      }
      function apply() {
        reframeIfNeeded();
        if (Tsum) {
          var s = sumVec();
          /* ⚠️ 列主序，**必须 16 个数**：我第一版只写了 14 个，MathBox 当场抛错（整张图里的和向量就没了） */
          Tsum.set("matrix", [s[0], 0, 0, 0, 0, s[1], 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
        }
        if (para) {
          /* 列主序 16 个数：第 1 列 = c₁v₁，第 2 列 = c₂v₂ */
          var ax = coefs[0] * vecs[0][0], ay = coefs[0] * vecs[0][1];
          var bx = coefs[1] * vecs[1][0], by = coefs[1] * vecs[1][1];
          para.set("matrix", [ax, ay, 0, 0, bx, by, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
        }
        var s = sumVec();
        /* 读数按公式排（P-042）：`1.5v_1+0.8v_2=(2.30,\ 1.10)`，长度另起一句人话 */
        var tex = vecs.map(function (v, i) {
          var coef = coefs[i].toFixed(1);
          return (i === 0 ? "" : (coefs[i] >= 0 ? "+" : "-")) + coef + "v_{" + (i + 1) + "}";
        }).join("");
        readout.innerHTML = texInline(tex + " = (" + s[0].toFixed(2) + ",\\ " +
          s[1].toFixed(2) + ")") + "<span class='mdl-note'>长度 " +
          Math.hypot(s[0], s[1]).toFixed(2) + "</span>";
      }
      var rows = [];
      coefs.forEach(function (c, i) {
        rows.push(sliderRow("系数 c" + (i + 1), c, [sliderLo, sliderHi], step, function (v) {
          coefs[i] = v; apply();
        }));
      });
      var box = document.createElement("div");
      box.className = "mdl-steps";
      rows.forEach(function (r) { box.appendChild(r.el); });
      ui.insertBefore(box, ui.firstChild);

      var steps = (spec.steps || []).filter(function (s) { return s && s.state; });
      if (steps.length >= 2) {
        var idx = 0, label = document.createElement("span");
        label.className = "mdl-step-label";
        var bar = document.createElement("div");
        bar.className = "mdl-steps";
        function go(n) {
          idx = Math.max(0, Math.min(steps.length - 1, n));
          var st = steps[idx].state || {};
          if (Array.isArray(st.coefficients)) {
            st.coefficients.forEach(function (v, i) {
              if (i < coefs.length) {
                coefs[i] = clampNum(v, sliderLo, sliderHi, coefs[i]);
                rows[i].input.value = coefs[i];
                rows[i].out.textContent = (+coefs[i]).toFixed(2);
              }
            });
          }
          if (Array.isArray(st.vectors)) {
            st.vectors.forEach(function (v, i) { if (i < vecs.length) vecs[i] = [num(v[0], vecs[i][0]), num(v[1], vecs[i][1])]; });
          }
          apply();
          label.textContent = "第 " + (idx + 1) + " / " + steps.length + " 步：" + (steps[idx].label || "");
        }
        bar.appendChild(button("上一步", "", function () { go(idx - 1); }));
        bar.appendChild(button("下一步 ▶", "primary", function () { go(idx + 1); }));
        bar.appendChild(button("回到开始", "", function () { go(0); }));
        bar.appendChild(label);
        ui.appendChild(bar);
        go(0);
      }
      ui.appendChild(button("重置视角", "", g.reset));
      apply();
    });
  }

  /* ── 模板：消元分步（symbolic，KaTeX 排版，不进画布） ─────────────── */
  /* 符号型模型的"一行"：数字数组 → 用 & 拼；字符串 → 原样（它本来就是 LaTeX）。
     两种形态：
       * form=matrix（默认）：增广矩阵，圆括号；augmented=true 时在最后一列前画竖线
       * form=system：方程组，左侧大括号 + 每行一个方程（§2.1 还没引入矩阵，用它） */
  function rowsToTex(rows, opts) {
    var opts2 = opts || {};
    var lines = (rows || []).map(function (row) {
      if (typeof row === "string") return row;
      return (row || []).map(function (v) {
        return typeof v === "number" ? String(v) : esc(v);
      }).join(" & ");
    });
    if (opts2.form === "system") {
      return "\\left\\{\\begin{array}{l}" + lines.join(" \\\\ ") + "\\end{array}\\right.";
    }
    var cols = ((rows || [])[0] || []).length;
    var spec = "c".repeat(Math.max(1, cols - (opts2.augmented && cols > 1 ? 1 : 0)));
    if (opts2.augmented && cols > 1) spec += "|c";
    return "\\left(\\begin{array}{" + spec + "}" + lines.join(" \\\\ ") + "\\end{array}\\right)";
  }

  function renderEliminationSteps(stage, ui, spec) {
    var params = spec.params || {};
    var steps = (params.steps || []).filter(function (s) { return s && (s.matrix || s.rows); });
    var box = document.createElement("div");
    box.className = "sym-steps";
    var idx = 0, timer = null;

    function draw() {
      var st = steps[idx] || {};
      box.innerHTML =
        '<div class="sym-progress">第 ' + (idx + 1) + " / " + steps.length + " 步" +
        (st.op ? '　<span class="sym-op">' + esc(st.op) + "</span>" : "") + "</div>" +
        '<div class="sym-matrix">' + renderTex(rowsToTex(st.matrix || st.rows, params)) + "</div>" +
        (st.note ? '<div class="sym-note">' + esc(st.note) + "</div>" : "") +
        '<ol class="sym-dots">' + steps.map(function (s, i) {
          return '<li class="' + (i === idx ? "on" : "") + '" data-i="' + i + '">' + (i + 1) + "</li>";
        }).join("") + "</ol>";
      Array.prototype.forEach.call(box.querySelectorAll(".sym-dots li"), function (li) {
        li.addEventListener("click", function () { stop(); idx = +li.dataset.i; draw(); });
      });
    }
    function stop() { if (timer) { clearInterval(timer); timer = null; } playBtn.textContent = "自动播放"; }
    function play() {
      if (timer) return stop();
      playBtn.textContent = "暂停";
      timer = setInterval(function () {
        if (idx >= steps.length - 1) { stop(); return; }
        idx++; draw();
      }, 1400);
    }
    function renderTex(tex) {
      try {
        return global.katex.renderToString(tex, { displayMode: true, throwOnError: false, strict: false });
      } catch (e) { return "<code>" + esc(tex) + "</code>"; }
    }
    var prevBtn = button("上一步", "", function () { stop(); idx = Math.max(0, idx - 1); draw(); });
    var nextBtn = button("下一步 ▶", "primary", function () { stop(); idx = Math.min(steps.length - 1, idx + 1); draw(); });
    var playBtn = button("自动播放", "", play);
    var resetBtn = button("回到第 1 步", "", function () { stop(); idx = 0; draw(); });
    if (!steps.length) {
      stage.innerHTML = '<p class="model-fallback">这一步的分步数据没给全，先看正文。</p>';
      return;
    }
    stage.appendChild(box);
    [prevBtn, nextBtn, playBtn, resetBtn].forEach(function (b) { ui.appendChild(b); });
    draw();
  }

  /* ── 模板：向量积（三维，只在真需要空间感的节出现） ──────────────────
     P-003：三维模型**不是"能拖的装饰"** —— 必须有：
       * `view`：一开始从哪个方向看（方位角/仰角）；
       * `view_hint`：怎么转、先看哪里、这一步看到什么（渲染层印在图上，见 render-core）；
       * 读数：|a×b| = a、b 张成的平行四边形面积；方向与 a、b 都垂直。 */
  function renderCrossProduct3D(stage, ui, spec) {
    var params = spec.params || {};
    function vec3(value, fallback) {
      var v = Array.isArray(value) ? value : [];
      return [num(v[0], fallback[0]), num(v[1], fallback[1]), num(v[2], fallback[2])];
    }
    var a = vec3(params.a, [1, 0, 0]);
    var b = vec3(params.b, [0, 1, 0]);
    var show = params.show || ["grid", "axes", "parallelogram", "normal"];
    function cross(u, v) {
      return [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    }
    function norm(u) { return Math.hypot(u[0], u[1], u[2]); }
    var c = cross(a, b);
    /* 取景：把 a、b、a×b 都装进去（三维里最容易"东西跑出画面"，实测过） */
    var reach = Math.max(norm(a), norm(b), norm(c), 1);
    var half = Math.min(6, Math.max(1.5, Math.ceil(reach * 1.25 * 10) / 10));
    var view = Array.isArray(params.view) && params.view.length === 2 ? params.view : [40, 25];

    return bootCanvas(stage, half, { view: view }).then(function (g) {
      var range = [[-half, half], [-half, half], [-half, half]];
      var cart = g.mb.cartesian({ range: range, scale: [1, 1, 1] });
      if (show.indexOf("grid") >= 0) {
        /* ⚠️ MathBox 2.3.1 的 `grid` **只认 lineX/lineY**：写 lineZ 会当场抛
           「Setting unknown property」（实测：整张 3D 图直接渲染失败）。
           所以三维里仍然是"一张 xy 网格 + 三根坐标轴"，空间感靠旋转与箭头本身。 */
        cart.grid({ lineX: true, lineY: true, color: "#e8eef5", width: 1 });
      }
      if (show.indexOf("axes") >= 0) {
        cart.axis({ axis: 1, color: COLORS.axis, width: 2 });
        cart.axis({ axis: 2, color: COLORS.axis, width: 2 });
        cart.axis({ axis: 3, color: COLORS.axis, width: 2 });
      }
      /* a、b 用 vector（两点 line 在 MathBox 2.3.1 里什么都不画，实测） */
      cart.array({ data: [[0, 0, 0], a], channels: 3, items: 2 })
        .vector({ width: 6, color: COLORS.primary, start: false, end: true, zBias: 9 });
      cart.array({ data: [[0, 0, 0], b], channels: 3, items: 2 })
        .vector({ width: 6, color: COLORS.secondary, start: false, end: true, zBias: 9 });
      if (show.indexOf("parallelogram") >= 0) {
        /* 平行四边形 = 单位正方形 + 矩阵（face 不接受 data，实测）：
           第 1 列 = a，第 2 列 = b */
        var T = cart.transform({ matrix: [a[0], a[1], a[2], 0, b[0], b[1], b[2], 0,
                                          0, 0, 1, 0, 0, 0, 0, 1] });
        T.array({ data: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], channels: 3, items: 4 })
          .face({ color: COLORS.emphasized, opacity: 0.28, zBias: -3 });
      }
      if (show.indexOf("normal") >= 0) {
        cart.array({ data: [[0, 0, 0], c], channels: 3, items: 2 })
          .vector({ width: 8, color: "#7c3aed", start: false, end: true, zBias: 12 });
      }
      var readout = document.createElement("div");
      readout.className = "mdl-readout";
      readout.innerHTML = texInline("\\boldsymbol{a}\\times\\boldsymbol{b} = (" +
        c.map(function (v) { return v.toFixed(2); }).join(",\\ ") + ")") +
        "<span class='mdl-note'>|a×b| = " + norm(c).toFixed(2) +
        "（= a、b 张成的平行四边形面积）　a·(a×b) = " + (a[0] * c[0] + a[1] * c[1] + a[2] * c[2]).toFixed(2) +
        "，b·(a×b) = " + (b[0] * c[0] + b[1] * c[1] + b[2] * c[2]).toFixed(2) + "</span>";
      ui.appendChild(readout);
      /* 视角按钮：正面 / 俯视 / 侧视 —— 给"该从哪看"一个可点的落点 */
      var LOOKS = [["正面看（沿 z 轴）", [90, 0]], ["俯视（从上往下）", [90, 89]],
                   ["侧看 a、b 张开的样子", view]];
      var bar = document.createElement("div");
      bar.className = "mdl-steps";
      LOOKS.forEach(function (pair) {
        bar.appendChild(button(pair[0], "", function () {
          var dist = Math.hypot.apply(null, g.three.camera.position.toArray());
          var az = pair[1][0] * Math.PI / 180, el = pair[1][1] * Math.PI / 180;
          g.reset([dist * Math.cos(el) * Math.cos(az), dist * Math.sin(el),
                   dist * Math.cos(el) * Math.sin(az)]);
        }));
      });
      ui.appendChild(bar);
      ui.appendChild(button("重置视角", "", g.reset));
    });
  }

  /* Fixed analytic SVG: model output supplies two coordinates, never formulas/code. */
  var harmonicSequence = 0;
  function renderHarmonicContours(stage, ui, spec) {
    var params = spec.params;
    if (!params || Array.isArray(params) || Object.keys(params).some(function (key) {
      return key !== "initial_point" && key !== "extent";
    }) || typeof params.extent !== "number" || !Number.isFinite(params.extent)
      || params.extent < 1 || params.extent > 4 || !Array.isArray(params.initial_point)
      || params.initial_point.length !== 2 || params.initial_point.some(function (value) {
        return typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > params.extent;
      })) throw new Error("harmonic-contours 需要域内有限坐标及 1–4 的 extent；不接受公式或代码");

    var extent = params.extent, point = params.initial_point.slice();
    var size = 520, margin = 44, span = size - 2 * margin;
    var prefix = "harmonic-" + (++harmonicSequence);
    var figure = stage.closest(".model");
    if (figure) { figure.classList.remove("model-canvas"); figure.classList.add("model-svg"); }
    stage.replaceChildren(); ui.replaceChildren();
    ui.classList.add("harmonic-ui");
    function svgEl(tag, attrs, parent, text) {
      var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
      Object.keys(attrs || {}).forEach(function (key) { el.setAttribute(key, attrs[key]); });
      if (text != null) el.textContent = text;
      if (parent) parent.appendChild(el);
      return el;
    }
    function sx(x) { return margin + (x + extent) * span / (2 * extent); }
    function sy(y) { return size - margin - (y + extent) * span / (2 * extent); }
    function pair(p) { return sx(p[0]).toFixed(3) + "," + sy(p[1]).toFixed(3); }
    function line(a, b) { return "M" + pair(a) + "L" + pair(b); }
    function pathOf(points) { return points.map(function (p, i) { return (i ? "L" : "M") + pair(p); }).join(" "); }
    function fmt(value) { return (Math.abs(value) < 0.0005 ? 0 : value).toFixed(2); }
    function vectorText(v) { return "(" + fmt(v[0]).replace("-", "−") + ", " + fmt(v[1]).replace("-", "−") + ")"; }
    var svg = svgEl("svg", {viewBox:"0 0 520 520", class:"harmonic-plot", tabindex:"0",
      "aria-label":"f(z)=z² 的实部与虚部等值线。拖动选点，或用方向键每次移动 0.05；Home 回到原点。"}, stage);
    svgEl("title", {}, svg, "共轭调和函数：等值线与正交关系");
    var defs = svgEl("defs", {}, svg);
    var clip = svgEl("clipPath", {id:prefix + "-clip"}, defs);
    svgEl("rect", {x:margin,y:margin,width:span,height:span}, clip);
    ["u", "v"].forEach(function (name) {
      var marker = svgEl("marker", {id:prefix + "-" + name,viewBox:"0 0 10 10",refX:9,refY:5,
        markerWidth:7,markerHeight:7,orient:"auto-start-reverse"}, defs);
      svgEl("path", {d:"M0,0 L10,5 L0,10 Z",fill:name === "u" ? "#2563eb" : "#b45309"}, marker);
    });
    svgEl("rect", {x:margin,y:margin,width:span,height:span,fill:"#fafcff",stroke:"#cbd5e1"}, svg);
    for (var tick = Math.ceil(-extent); tick <= Math.floor(extent); tick++) {
      svgEl("path", {d:line([tick,-extent],[tick,extent]) + " " + line([-extent,tick],[extent,tick]),
        stroke:tick === 0 ? "#64748b" : "#e2e8f0",fill:"none","stroke-width":tick === 0 ? 1.5 : 1}, svg);
      svgEl("text", {x:sx(tick),y:size-margin+21,"text-anchor":"middle",class:"harmonic-axis-label"}, svg, tick);
      if (tick !== 0) svgEl("text", {x:margin-12,y:sy(tick)+4,"text-anchor":"end",class:"harmonic-axis-label"}, svg, tick);
    }
    svgEl("text", {x:size-margin+19,y:sy(0)+5,class:"harmonic-axis-label"}, svg, "x");
    svgEl("text", {x:sx(0)+10,y:margin-15,class:"harmonic-axis-label"}, svg, "y");
    var clipped = svgEl("g", {"clip-path":"url(#"+prefix+"-clip)"}, svg);
    var curves = {}, tangents = {}, gradients = {};
    ["u", "v"].forEach(function (name) {
      var color = name === "u" ? "#2563eb" : "#b45309";
      curves[name] = svgEl("path", {"data-contour":name,stroke:color,"stroke-width":3,fill:"none"}, clipped);
      tangents[name] = svgEl("path", {"data-tangent":name,stroke:color,"stroke-width":1.6,
        "stroke-dasharray":"7 5",fill:"none"}, clipped);
      gradients[name] = svgEl("path", {"data-gradient":name,stroke:color,"stroke-width":3,fill:"none",
        "marker-end":"url(#"+prefix+"-"+name+")"}, clipped);
    });
    var rightAngle = svgEl("path", {stroke:"#334155","stroke-width":1.5,fill:"none"}, clipped);
    var dot = svgEl("circle", {r:7,fill:"#172554",stroke:"white","stroke-width":2,class:"harmonic-point"}, svg);
    var pointLabel = svgEl("text", {class:"harmonic-point-label"}, svg);
    var intro = document.createElement("p"); intro.className = "harmonic-intro";
    intro.textContent = "固定 f(z)=z²：蓝线 u=x²−y²，棕线 v=2xy。拖动图中选点或调节坐标，观察穿过该点的两条等值线。";
    ui.appendChild(intro);
    var sliders = [sliderRow("x 坐标", point[0], [-extent,extent], 0.01, function (x) { point[0]=x; paint(); }),
                   sliderRow("y 坐标", point[1], [-extent,extent], 0.01, function (y) { point[1]=y; paint(); })];
    sliders.forEach(function (slider, i) { slider.input.dataset.axis=i ? "y" : "x"; ui.appendChild(slider.el); });
    var options = document.createElement("div"); options.className = "harmonic-options";
    function toggle(label, checked) {
      var holder=document.createElement("label"), input=document.createElement("input");
      input.type="checkbox"; input.checked=checked; input.addEventListener("change",paint);
      holder.append(input,document.createTextNode(label)); options.appendChild(holder); return input;
    }
    var showTangents=toggle("显示切线（虚线）",true), showGradients=toggle("显示梯度方向（箭头）",false);
    options.appendChild(button("查看原点例外", "", function () { point=[0,0]; paint(); }));
    options.appendChild(button("重置选点", "", function () { point=params.initial_point.slice(); paint(); }));
    ui.appendChild(options);
    var values = {}, readout = document.createElement("div"); readout.className="harmonic-readout";
    [["u","u"],["v","v"],["grad-u","∇u"],["grad-v","∇v"],["dot","∇u · ∇v"],["angle","切线夹角"]].forEach(function (entry) {
      var row=document.createElement("div"), label=document.createElement("span"), output=document.createElement("output");
      label.textContent=entry[1]+" = "; output.dataset.harmonicValue=entry[0]; values[entry[0]]=output;
      row.append(label,output); readout.appendChild(row);
    });
    ui.appendChild(readout);
    var status=document.createElement("p"); status.className="harmonic-status"; status.setAttribute("aria-live","polite"); ui.appendChild(status);

    // Each analytic branch is sampled independently; no segment crosses a pole.
    function contourU(c) {
      if (c === 0) return line([-extent,-extent],[extent,extent])+" "+line([-extent,extent],[extent,-extent]);
      var bound=Math.sqrt(Math.max(0,extent*extent-Math.abs(c))), paths=[];
      [-1,1].forEach(function (sign) {
        var points=[];
        for (var i=0;i<=160;i++) {
          var t=-bound+2*bound*i/160, other=sign*Math.sqrt(t*t+Math.abs(c));
          points.push(c>0 ? [other,t] : [t,other]);
        }
        paths.push(pathOf(points));
      }); return paths.join(" ");
    }
    function contourV(c) {
      if (c === 0) return line([-extent,0],[extent,0])+" "+line([0,-extent],[0,extent]);
      // Compute both coordinates in log space: c/(2*extent) can underflow
      // even when c is a valid finite subnormal number.
      var logHalf=Math.log(Math.abs(c))-Math.log(2), logExtent=Math.log(extent), paths=[];
      [-1,1].forEach(function (sign) {
        var points=[];
        for (var i=0;i<=160;i++) {
          var logX=logHalf-logExtent+(2*logExtent-logHalf)*i/160;
          points.push([sign*Math.exp(logX),Math.sign(c)*sign*Math.exp(logHalf-logX)]);
        }
        paths.push(pathOf(points));
      }); return paths.join(" ");
    }
    function paint() {
      var x=point[0], y=point[1], u=x*x-y*y, v=2*x*y, radius=Math.hypot(x,y), singular=radius===0;
      curves.u.setAttribute("d",contourU(u)); curves.v.setAttribute("d",contourV(v));
      dot.setAttribute("cx",sx(x)); dot.setAttribute("cy",sy(y));
      pointLabel.setAttribute("x",sx(x)+(x>extent*0.6 ? -12 : 12)); pointLabel.setAttribute("y",sy(y)+(y>extent*0.6 ? 22 : -12));
      pointLabel.setAttribute("text-anchor",x>extent*0.6 ? "end" : "start"); pointLabel.textContent="P "+vectorText(point);
      values.u.textContent=fmt(u); values.v.textContent=fmt(v);
      values['grad-u'].textContent=vectorText([2*x,-2*y]); values['grad-v'].textContent=vectorText([2*y,2*x]);
      values.dot.textContent="0.00"; values.angle.textContent=singular ? "不适用" : "90°";
      sliders.forEach(function (slider,i) { slider.input.value=point[i]; slider.out.textContent=fmt(point[i]); });
      ["u","v"].forEach(function (name) {
        tangents[name].setAttribute("visibility",!singular && showTangents.checked ? "visible" : "hidden");
        gradients[name].setAttribute("visibility",!singular && showGradients.checked ? "visible" : "hidden");
      });
      rightAngle.setAttribute("visibility",!singular && showTangents.checked ? "visible" : "hidden");
      if (!singular) {
        var tu=[y/radius,x/radius], tv=[x/radius,-y/radius];
        function offset(direction,length) { return [x+direction[0]*length,y+direction[1]*length]; }
        tangents.u.setAttribute("d",line(offset(tu,-2*extent),offset(tu,2*extent)));
        tangents.v.setAttribute("d",line(offset(tv,-2*extent),offset(tv,2*extent)));
        gradients.u.setAttribute("d",line(point,offset(tv,extent*0.38)));
        gradients.v.setAttribute("d",line(point,offset(tu,extent*0.38)));
        var length=extent*0.12, a=offset(tu,length), b=offset(tv,length);
        rightAngle.setAttribute("d",pathOf([a,[a[0]+tv[0]*length,a[1]+tv[1]*length],b]));
      }
      status.classList.toggle("is-singular",singular);
      status.textContent=singular
        ? "原点：∇u = ∇v = (0, 0)，f′(0)=0。u=0 是两条对角线，v=0 是两条坐标轴；两族在此均无唯一正则切线，不能套用正交结论。"
        : "非原点：两梯度非零且点积为 0，因此两条等值线的切线夹角为 90°。箭头仅表示梯度方向，长度已缩放；真实梯度见读数。";
    }
    function selectAt(event) {
      var rect=svg.getBoundingClientRect(), scale=Math.min(rect.width,rect.height)/size;
      var left=rect.left+(rect.width-size*scale)/2, top=rect.top+(rect.height-size*scale)/2;
      point=[((event.clientX-left)/scale-margin)/span*2*extent-extent,
             extent-((event.clientY-top)/scale-margin)/span*2*extent].map(function (n) {
        return Math.round(Math.max(-extent,Math.min(extent,n))*100)/100;
      }); paint();
    }
    var dragging=false;
    svg.addEventListener("pointerdown",function (event) {
      if (event.button !== 0) return; dragging=true; svg.setPointerCapture(event.pointerId); selectAt(event); event.preventDefault();
    });
    svg.addEventListener("pointermove",function (event) { if (dragging) selectAt(event); });
    ["pointerup","pointercancel","lostpointercapture"].forEach(function (name) { svg.addEventListener(name,function () { dragging=false; }); });
    svg.addEventListener("keydown",function (event) {
      var directions={ArrowLeft:[-0.05,0],ArrowRight:[0.05,0],ArrowUp:[0,0.05],ArrowDown:[0,-0.05]};
      if (event.key === "Home") point=[0,0];
      else if (directions[event.key]) point=point.map(function (n,i) { return Math.max(-extent,Math.min(extent,Math.round((n+directions[event.key][i])*100)/100)); });
      else return;
      event.preventDefault(); paint();
    });
    paint();
  }

  /* ── 挂载 ─────────────────────────────────────────────────────────── */
  var TEMPLATES = {
    /* P-003：matrix-transform（拧单个数字）已改造成"整行动作"；老名字留一个键，
       老产物不至于变成白板（校验器也会把老模板名归一到 row-operation）。 */
    "row-operation": { kind: "canvas", render: renderRowOperation, aspect: 0.62 },
    "matrix-transform": { kind: "canvas", render: renderRowOperation, aspect: 0.62 },
    /* 三维只在真需要空间感的节出现；view_hint 是硬要求（校验器 R18） */
    "cross-product-3d": { kind: "canvas", render: renderCrossProduct3D, aspect: 0.66 },
    "vector-combination": { kind: "canvas", render: renderVectorCombination, aspect: 0.62 },
    "elimination-steps": { kind: "symbolic", render: renderEliminationSteps, aspect: 0.42 },
    "harmonic-contours": { kind: "svg", render: renderHarmonicContours }
  };

  function mount(figure, spec) {
    var stage = figure.querySelector(".model-stage");
    var ui = figure.querySelector(".model-ui");
    if (!stage || !ui) return;
    var tmpl = TEMPLATES[spec.template];
    if (!tmpl) {
      stage.innerHTML = '<p class="model-fallback">这个模型（' + esc(spec.template || "?") +
        "）还没有对应的画法，先看正文。</p>";
      return;
    }
    var fallback = spec.fallback_text || spec.caption || "";
    var run = function () {
      try {
        stage.dataset.modelId = spec.id;
        var ret = tmpl.render(stage, ui, spec);
        if (ret && typeof ret.then === "function") {
          ret.then(function () { report(); }, function (err) { failed(err); });
          return;
        }
        report();
      }
      catch (err) { failed(err); }
    };
    function report() {
      if (!global.console) return;
      var rect = stage.getBoundingClientRect();
      console.log("[模型] " + spec.id + " " + spec.template + " " + tmpl.kind +
        " 画布=" + Math.round(rect.width) + "x" + Math.round(rect.height));
      if (tmpl.kind !== "canvas") return;
      setTimeout(function () {
        try {
          var info = window.__MODEL_RENDERERS__ && window.__MODEL_RENDERERS__[spec.id];
          var d = stage.__diag || {};
          if (info) console.log("[模型] " + spec.id + " 绘制调用=" + info.calls +
            " 三角形=" + info.triangles + " 画布=" + info.w + "x" + info.h +
            "｜stage=" + d.w + "x" + d.h + " half=" + d.half + " 相机距离=" + d.dist);
        } catch (e) {}
      }, 1400);
    }
    function failed(err) {
      stage.innerHTML = '<p class="model-fallback">' + esc(fallback) + "</p>";
      figure.classList.add("model-failed");
      if (global.console) console.warn("[模型] " + spec.id + " 渲染失败：" + err.message);
    }
    if (tmpl.kind === "canvas") {
      stage.dataset.aspect = tmpl.aspect;
      stage.dataset.rotate = spec.rotate ? "1" : "";
      if (spec.camera) stage.dataset.camera = spec.camera;
      /* 画布模型要有兜底时间：MathBox/WebGL 起不来时**不能被无限等**（P-025-2 的
         "一直显示正在加载…"就是这种无限等）。12 秒还没画出东西就换成兜底说明，
         并如实打一行警告（页面自检会读这行）。 */
      window.setTimeout(function () {
        if (stage.dataset.modelId !== spec.id) return;        // 这个 stage 已经换过内容了
        if (stage.querySelector("canvas")) return;           // 已经有画布了，正常
        stage.innerHTML = '<p class="model-fallback">这个模型在这台机器上没能画出来。' +
          (fallback ? "　" + esc(fallback) : "") + "</p>";
        figure.classList.add("model-failed");
        if (global.console) console.warn("[模型] " + spec.id + " 超过 12 秒没有画出来");
      }, 12000);
      ensureMathBox().then(run).catch(function (err) {
        /* 不降级：就读正文那句兜底说明（规范：绝不写第二套渲染器） */
        stage.innerHTML = '<p class="model-fallback">这个模型需要支持 WebGL 的浏览器。' +
          (fallback ? "　" + esc(fallback) : "") + "</p>";
        figure.classList.add("model-failed");
        if (global.console) console.warn("[模型] MathBox 没加载：" + err.message);
      });
    } else {
      run();
    }
  }

  function mountAll(root, models) {
    var nodes = root.querySelectorAll("[data-model-id]");
    Array.prototype.forEach.call(nodes, function (figure) {
      var id = figure.getAttribute("data-model-id");
      var spec = (models || {})[id];
      if (!spec) return;
      /* 画布模型要等元素有宽度才量得准：下一帧再挂 */
      requestAnimationFrame(function () { requestAnimationFrame(function () { mount(figure, spec); }); });
    });
  }

  /** 把 root 里所有已挂载的模型销毁（app.js 换节之前叫它，P-025-1）。 */
  function disposeAll(root) {
    var scope = root || global.document;
    if (!scope || !scope.querySelectorAll) return;
    Array.prototype.forEach.call(scope.querySelectorAll("[data-model-id]"), function (stage) {
      if (typeof stage.__disposeModel === "function") {
        try { stage.__disposeModel(); } catch (err) {
          if (global.console) console.warn("[模型] 销毁出错：" + err.message);
        }
      }
      stage.__disposeModel = null;
    });
  }

  global.Models = { mount: mount, mountAll: mountAll, disposeAll: disposeAll,
                    TEMPLATES: TEMPLATES,
                    __test: { cardOf: null, squareRange: squareRange, gridSpec: gridSpec } };
})(window);
