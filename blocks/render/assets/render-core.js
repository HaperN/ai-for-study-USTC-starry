/* 渲染层的纯逻辑：把讲解 markdown 变成 HTML。
 *
 * 这里**不碰 DOM**，所以能在 node 里真跑测试（浏览器里没法自动验，node 能）。
 * 浏览器里由 app.js 传进 marked / katex；node 里由测试 require 那两个库传进来。
 *
 * 要处理三件事（顺序很要紧）：
 *   1. 数式 $...$ / $$...$$ 不能被 markdown 解析器弄坏 → 先挖出来存好，换成占位符；
 *   2. 概念引用 [[CONCEPT:id|显示文字]] / 插图 [[FIGURE:id]] 同理；
 *   3. markdown 解析完，再把占位符换成 KaTeX 渲染结果和 <a>/<figure> 标签。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require('./reader-markdown.js'),require('./study-math.js'));
  } else {
    root.DSHRenderCore = factory(root.DSHReaderMarkdown,root.DSHStudyMath);
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (readerMarkdown,studyMath) {
  "use strict";

  // Synchronous SHA-256 for the host's presentation binding (UTF-8 JSON).
  function presentationHash(text) {
    var bytes=Array.from(new TextEncoder().encode(text)), bit=bytes.length*8;
    bytes.push(128); while(bytes.length%64!==56) bytes.push(0);
    var hi=Math.floor(bit/4294967296),lo=bit>>>0;
    for(var n=3;n>=0;n--)bytes.push((hi>>>(n*8))&255);
    for(var n=3;n>=0;n--)bytes.push((lo>>>(n*8))&255);
    var h=[0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19], k=[],i=2;
    while(k.length<64){var prime=true;for(var d=2;d*d<=i;d++)if(i%d===0){prime=false;break;}if(prime)k.push((Math.pow(i,1/3)%1*4294967296)>>>0);i++;}
    function rr(x,n){return (x>>>n)|(x<<(32-n));}
    for(var off=0;off<bytes.length;off+=64){var w=[];
      for(i=0;i<16;i++)w[i]=((bytes[off+4*i]<<24)|(bytes[off+4*i+1]<<16)|(bytes[off+4*i+2]<<8)|bytes[off+4*i+3])>>>0;
      for(i=16;i<64;i++){var x=w[i-15],y=w[i-2];w[i]=(w[i-16]+(rr(x,7)^rr(x,18)^(x>>>3))+w[i-7]+(rr(y,17)^rr(y,19)^(y>>>10)))>>>0;}
      var a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7];
      for(i=0;i<64;i++){var t1=(z+(rr(e,6)^rr(e,11)^rr(e,25))+((e&f)^(~e&g))+k[i]+w[i])>>>0,t2=((rr(a,2)^rr(a,13)^rr(a,22))+((a&b)^(a&c)^(b&c)))>>>0;z=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}
      h=[(h[0]+a)>>>0,(h[1]+b)>>>0,(h[2]+c)>>>0,(h[3]+d)>>>0,(h[4]+e)>>>0,(h[5]+f)>>>0,(h[6]+g)>>>0,(h[7]+z)>>>0];
    }
    return 'sha256:'+h.map(function(v){return v.toString(16).padStart(8,'0');}).join('');
  }

  // CONCEPT / FIGURE 带「|显示文字」，NOTE 不带（它是个上标小标记，内容在下面的批注区）
  var REF = /\[\[(CONCEPT|FIGURE|NOTE|DIAGRAM|MODEL|EXPLAIN|EXAMPLE|MEDIA)\s*:\s*([^\]|]*?)\s*(?:\|\s*([^\]]*?)\s*)?\]\]/g;
  var SLOT = "\u0000";
  /* P-035：正文里的「线性方程组 (2.1)」要能点着跳到**原文对照里的 (2.1)**。
     用户原话：「可以把线性方程组（2.1)做成超链接，或是能够直接跳转到 2.1位置的链接，
     毕竟这次能够直接看到原文，下次未必看得到」。
     ⚠️ 这个正则和 `lib/refs.py` 里的 `NUMBER_REF` **必须一致**（一处定义在那边，
     这里是它在浏览器里的同款：Python 检查"有没有地方可跳"，JS 负责"做成链接"）。 */
  var NUM_REF = /[（(]\s*(\d+(?:\.\d+)+)\s*[）)]/g;

  function numAnchorId(number) {
    return "num-" + String(number).replace(/\./g, "-");
  }

  /* 在**已经 mask 过的 markdown 文字**上做替换（公式与引用都进了仓库）：
     这样绝不会改到公式内部的编号，也不会改到 HTML 属性 —— 少一整类坑。
     `index` 是"编号 → 哪些节的原文里有它"；`unitId` 是当前节。三者一起决定：
       · 本节原文里有 → 本节内跳（`data-num-unit` 留空）
       · 只有别的节有 → 跳到那一节（跨节跳转，仍然跳得到，不是死链）
       · 谁都没有     → **保持纯文本**（宁可不做链接，也不做死链） */
  function linkNumbers(text, index, unitId) {
    if (!index) return text;
    return String(text == null ? "" : text).replace(NUM_REF, function (whole, number) {
      var where = index[number];
      if (!where || !where.length) return whole;
      var target = where.indexOf(unitId) >= 0 ? "" : where[0];
      return '<a class="numref" href="#' + numAnchorId(number) + '" data-num="' +
        number + '"' + (target ? ' data-num-unit="' + target + '"' : "") +
        ' title="跳到原文对照里的 ' + whole + '">' + whole + "</a>";
    });
  }

  /** 原文那一侧：给编号打锚点，好让正文里的链接跳进来。 */
  function anchorNumbers(text) {
    return String(text == null ? "" : text).replace(NUM_REF, function (whole, number) {
      return '<span class="num-target" id="' + numAnchorId(number) + '">' + whole + "</span>";
    });
  }

  /* 把 $...$ / $$...$$ 按**逐字符扫描**切出来，而不是用正则。
   *
   * 为什么正则不行（实测两种写法都真实存在，正则只能顾一头）：
   *   ① `$$A$$ 与 $$B$$` —— 一行里两条显示公式，正则能吃；
   *   ② `$A$$B$`         —— 两个相邻的行内公式，中间那个 `$$` 是**两个行内定界符撞在一起**，
   *                          正则会把这里当显示公式起点，把中间整段吞掉（实测：一处公式
   *                          渲染失败 + 13 个内部槽位泄漏到页面上）。
   * 扫描规则很朴素：遇到 `$` 先数连着的几个：
   *   • 连着 2 个及以上 → 当显示公式，找下一个 `$$` 收尾；
   *   • 只有 1 个 → 当行内公式，找下一个 `$` 收尾（不管它是不是某个 `$$` 的第一个）。
   * 这样 ① 走"显示"、② 走"行内"，两种都对。
   */
  function sliceSpans(text) {
    var out = [];            // [{raw, start, end}]
    var s = String(text == null ? "" : text);
    var i = 0;
    while (i < s.length) {
      /* `\(…\)` / `\[…\]` 也认：数据层已经会归一成 `$`，这里是**显示层兜底** ——
         万一哪份老产物没归一，页面也不该露出裸 LaTeX（实测例题里就这么露过 43~110 处）。 */
      if (s.charAt(i) === "\\" && (s.charAt(i + 1) === "(" || s.charAt(i + 1) === "[")) {
        var closeTok = s.charAt(i + 1) === "(" ? "\\)" : "\\]";
        var closeAt = s.indexOf(closeTok, i + 2);
        if (closeAt >= 0) {
          out.push({ raw: s.slice(i, closeAt + 2), start: i, end: closeAt + 2 });
          i = closeAt + 2;
          continue;
        }
        i += 2;
        continue;
      }
      if (s.charAt(i) !== "$") { i += 1; continue; }
      var run = 1;
      while (s.charAt(i + run) === "$") { run += 1; }
      if (run >= 2) {
        var close = s.indexOf("$$", i + 2);
        if (close >= 0) {                     // 找得到收尾 → 显示公式
          out.push({ raw: s.slice(i, close + 2), start: i, end: close + 2 });
          i = close + 2;
          continue;
        }
        // 找不到收尾（数据被切断过，实测发生过）→ 当成"到文末的独立公式"，
        // 宁可显示成公式，也别把 LaTeX 裸露在页面上
        out.push({ raw: s.slice(i) + "$$", start: i, end: s.length });
        i = s.length;
        continue;
      }
      var single = s.indexOf("$", i + 1);      // 行内公式
      if (single < 0) { i += run; continue; }
      if (single === i + 1 && run >= 2) {      // `$$` 后面紧接 `$`：整体当不了公式，跳过
        i += 1;
        continue;
      }
      out.push({ raw: s.slice(i, single + 1), start: i, end: single + 1 });
      i = single + 1;
    }
    return out;
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* ── P-027：模型把术语写了两遍，页面上就出现「…的解集 解集。」 ─────────────
     用户 2026-09-25 的审查里点到这一条。实测这一批 9 份教案里 **8 处**都是同一个毛病：
     模型很自然地写「称为线性方程组的解集 [[CONCEPT:C-0014|解集]]」—— 词写一遍、链接再写一遍。
     光靠提示词管不住（换个说法又会出现），所以在**渲染这一处**做确定性收口：
       若链接的显示文字**紧接在前面已经写过的同一个词**之后，就把前面那个词并进链接里。
     这么做在屏幕上是**逐字不变**的（同样的几个字，只是"可点"落在了合并后的那个词上），
     所以不存在"渲染层偷偷改内容"的问题；`lib/refs.py` 里那条检查用的是同一判据（一处规矩两处用）。
     为什么放在 `mask` 里：所有正文/例题/批注/原文都要经过它，改一处就是全局生效。 */
  function dedupeConceptNames(text, concepts) {
    if (!concepts) return text;
    var src = String(text == null ? "" : text);
    var re = new RegExp(REF.source, "g");
    var out = "", last = 0, match;
    while ((match = re.exec(src))) {
      var before = src.slice(last, match.index);
      var type = match[1], id = String(match[2] || "").trim();
      var display = String(match[3] || "").trim();
      if (type === "CONCEPT") {
        var concept = concepts[id];
        var label = display || (concept && concept.name) || "";
        var trimmed = before.replace(/\s+$/, "");
        if (label && trimmed.length >= label.length &&
            trimmed.slice(trimmed.length - label.length) === label) {
          before = trimmed.slice(0, trimmed.length - label.length);
        }
      }
      out += before + match[0];
      last = match.index + match[0].length;
    }
    return out + src.slice(last);
  }

  function mask(text, store, concepts) {
    var stash = function (item) {
      store.push(item);
      return SLOT + (store.length - 1) + SLOT;
    };
    var out = dedupeConceptNames(text, concepts);
    /* ⚠️ 顺序很要紧：**先收引用，再收公式**（这一版改的）。
       反过来的话：`[[EXPLAIN:F-02|换行让 $a_{11}$ 非零]]` 里的 `$a_{11}$` 会**先**被挖成一个内部槽位，
       于是这个槽位跑进了引用的"显示文字"里；等到展开引用时，那个槽位属于**上一趟的仓库**，
       谁也展不开它 —— 结果页面上真的漏出 `\u00000\u0000`（实测：整章跑出来的 §2.3.1 页面自检报了
       "内部占位符 2 个泄漏"，两处都在这一个胶囊上）。
       先收引用之后：显示文字整段留给 renderInline 去处理（它自己会再收一次公式），槽位不会再跨仓库。 */
    out = out.replace(REF, function (_all, type, id, display) {
      return stash({ kind: "ref", type: type, id: String(id || "").trim(),
                     display: String(display || "").trim() });
    });
    // 从后往前替换，这样前面的下标不会被打乱
    sliceSpans(out).reverse().forEach(function (span) {
      out = out.slice(0, span.start) + stash({ kind: "math", raw: span.raw }) + out.slice(span.end);
    });
    return out;
  }

  /* ── 修改计划 R3：短标签里的裸 TeX ──────────────────────────────────────
     例题标题、批注标题、链接文字、目录名有时写成 `e^{1+i}`、`w=\sqrt[n]{z}`，
     不在 `$…$` 里，原来按纯文本转义，页面露出源码。这里只在已收走引用和公式之后，
     把**带 _ ^ \ 标记、没有普通英文单词、命令都认识、括号配平**的片段收成公式槽；
     中文叙述、编号、路径和已有 `$…$` 原样不动。 */
  var BARE_TOKEN = "A-Za-z0-9\\\\_^{}\\[\\]=+\\-*\\/().,'|<>≠≤≥±×−·→↦⇒⇔≈≡∞∂∫∮∑∏√∈′″α-ωΑ-Ωϑϕϵ";
  var BARE_FUNCTIONS = /(^|[^\\A-Za-z])(arcsin|arccos|arctan|sinh|cosh|tanh|sin|cos|tan|cot|exp|log|ln|arg|Arg|Ln|Re|Im|lim|max|min)(?![A-Za-z])/g;
  var BARE_MATH = new RegExp("[" + BARE_TOKEN + "]+(?:[ \t]+[" + BARE_TOKEN + "]+)*", "g");
  var BARE_COMMANDS = /^(?:arcsin|arccos|arctan|geqslant|leqslant|ll|gg|circ|angle|perp|parallel|forall|exists|setminus|emptyset|varnothing|Leftrightarrow|leftrightarrow|iff|longrightarrow|mid|vert|langle|rangle|ell|bigl|bigr|Bigl|Bigr|displaystyle|boldsymbol|mathbf|mathcal|widehat|widetilde|underline|binom|neg|cot|sec|csc|max|min|sup|inf|det|deg|bar|overline|hat|tilde|vec|frac|dfrac|tfrac|sqrt|cdot|cdots|ldots|dots|times|div|pm|mp|ne|neq|le|leq|ge|geq|approx|equiv|sim|to|mapsto|rightarrow|in|left|right|quad|mathrm|mathbb|operatorname|ln|log|exp|sin|cos|tan|sinh|cosh|tanh|arg|Arg|Ln|Re|Im|lim|sum|prod|int|oint|partial|nabla|prime|alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega|infty)$/;

  function bareMathExpression(raw) {
    var trail = raw.match(/[.,\s]*$/)[0];
    var expr = raw.slice(0, raw.length - trail.length);
    if (!/[_^\\=<>≠≤≥∞∂∫∮∑∏√∈α-ωΑ-Ω]/.test(expr) && !/^[A-Za-z](?:\d+)?$/.test(expr)) return null;
    var known = true;
    var letters = expr.replace(/\\(?:mathrm|operatorname|text)\{[^{}]*\}/g, "")
      .replace(/\\([A-Za-z]+)/g, function (_all, command) {
        if (!BARE_COMMANDS.test(command)) known = false;
        return "";
      });
    // Function names may be written without a backslash (cos4φ) and short
    // letter runs are implicit products (nz^{n−1}); four or more letters are prose.
    if (!known || /[A-Za-z]{4,}/.test(letters.replace(BARE_FUNCTIONS, '$1'))) return null;
    expr = expr.replace(BARE_FUNCTIONS, '$1\\$2');
    var depth = 0, square = 0;
    for (var i = 0; i < expr.length; i += 1) {
      var ch = expr.charAt(i);
      if (ch === "{") depth += 1; else if (ch === "}") depth -= 1;
      else if (ch === "[") square += 1; else if (ch === "]") square -= 1;
      if (depth < 0 || square < 0) return null;
    }
    if (depth || square) return null;
    return {expr: expr, trail: trail};
  }

  /** 已 mask 的短文本：把裸 TeX 片段收进公式仓库（槽位由 \u0000 隔开，不会被跨越）。 */
  function maskBareMath(masked, store) {
    return String(masked).replace(BARE_MATH, function (raw) {
      var found = bareMathExpression(raw);
      if (!found) return raw;
      store.push({ kind: "math", raw: "$" + found.expr + "$" });
      return SLOT + (store.length - 1) + SLOT + found.trail;
    });
  }

  /* ── P-045：悬停提示里不许出现 LaTeX 源码 ────────────────────────────────
     用户原话：「悬停提示格式不正常（点击正常）」。定位到的是批注按钮上的
     **浏览器原生 `title`**：它只能显示纯文本，教案里写的是 `$j_2$ 的两个条件`，
     于是悬停出来就是带美元符和反斜杠的源码（点击后弹层里是 KaTeX，所以正常）。
     原生 tooltip 里放不了公式，所以这里把内联公式**捋成人话**：
       `$j_2$` → `j_2`（下标保留成下划线写法，读者看得懂）
       `\boldsymbol{a}` → `a`、`\cdot` → `·`、`\neq` → `≠` ……
     只在**属性**里用（内容区该渲染公式还是渲染公式）。 */
  var PLAIN_TEX = { cdot: "·", times: "×", to: "→", mapsto: "↦", neq: "≠", ne: "≠",
                    le: "≤", leq: "≤", ge: "≥", geq: "≥", pm: "±", mp: "∓",
                    in: "∈", notin: "∉", subset: "⊂", cup: "∪", cap: "∩",
                    infty: "∞", partial: "∂", nabla: "∇", approx: "≈", equiv: "≡",
                    rightarrow: "→", Rightarrow: "⇒", leftrightarrow: "↔",
                    varnothing: "∅", emptyset: "∅", ldots: "…", cdots: "…", dots: "…" };
  var PLAIN_CMD = new RegExp("\\\\(" + Object.keys(PLAIN_TEX)
    .sort(function (a, b) { return b.length - a.length; })
    .join("|") + ")(?![a-zA-Z])", "g");

  function plainMath(text) {
    var out = String(text == null ? "" : text);
    out = out.replace(/\$\$([^$]*)\$\$/g, " $1 ").replace(/\$([^$]*)\$/g, " $1 ");
    // 分数、根号先处理：等大括号被抹掉之后就认不出来了（`\frac{1}{2}` 会变成 "12"）
    out = out.replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "$1/$2");
    out = out.replace(/\\sqrt\s*\{([^{}]*)\}/g, "√($1)");
    /* ⚠️ 已知命令**必须在大括号类替换之前**做掉。顺序反了就会出事（实测）：
       `\boldsymbol{a}\cdot\boldsymbol{b}` 先把 `\boldsymbol{b}` 收成 `b`，
       字符串成了 `\cdotb`，于是命令名被读成 "cdotb" → 整段删掉 → 提示里成了 "a=0"，
       **意思都变了**。先做已知命令时 `\cdot` 后面还是 `\`，边界干净。 */
    out = out.replace(PLAIN_CMD, function (_whole, name) { return PLAIN_TEX[name]; });
    out = out.replace(/\\(?:boldsymbol|mathbf|mathrm|mathit|mathcal|mathbb|text|textbf|operatorname)\s*\{([^{}]*)\}/g,
                      "$1");
    out = out.replace(/\\(?:begin|end)\s*\{[^{}]*\}/g, "");     // 矩阵/方程组环境：留内容、去壳
    out = out.replace(/\\(?:left|right)\b/g, "");
    /* 公式里的换行 `\\` 要在"去掉未知命令"**之前**处理：反了的话，`a\\b` 的第二个反斜杠
       会被当成命令 `\b` 删掉，剩下一个孤零零的 `\`（实测输出 `(a\)`）。 */
    out = out.replace(/\\\\/g, " ");
    out = out.replace(/\\([a-zA-Z]+)/g, "");                      // 其余命令（大小、间距之类）直接去掉
    return out.replace(/[{}]/g, "").replace(/\s+/g, " ").trim();
  }

  function createRenderer(deps) {
    var marked = deps.marked;
    var katex = deps.katex;
    var concepts = deps.concepts || {};
    var figures = deps.figures || {};
    var diagrams = deps.diagrams || {};      // 过程图：全局 id → 结构化规格
    var models = deps.models || {};          // 交互模型：全局 id → 规格（相机/尺寸/颜色由 models.js 补）

    /* ── 本单元的"指向上下文"（P-002：边注要能指向；P-006：公式/定理要能点开详解）──
       用户原话：「边注不行，我想要的是可以指向的」。做法是**三种指向同时成立**：
         ① 正文里一个带编号的小圆点 ② 点它就地展开批注 ③ 右侧详解栏里**同号**的卡片。
       编号必须由**正文出现顺序**决定（教案里 [[NOTE:N-03]] 可能出现在 N-01 前面），
       所以在这里发号；app.js 渲染完正文后调 noteOrder()/explainOrder() 拿同一份顺序。
       数据仍然全在教案 JSON（哪句挂哪条批注是模型写的），渲染层只负责展开（G2）。 */
    var unitCtx = null;
    var popDepth = 0;                        // 批注里再出现批注/公式时防递归
    var NOTE_KIND = { note: "注", derive: "推导", pitfall: "易错", intuition: "直觉" };

    function beginUnit(ctx) {
      unitCtx = ctx || {};
      unitCtx.noteOrder = [];
      unitCtx.explainOrder = [];
    }

    /* 有些来源（原文、校验提醒）会把反斜杠写成两个：`\\left(`。LaTeX 里那是"换行 + left"，
       于是公式整段渲染失败 —— 这里只做**显示层**的收拢：`\\` 后面跟的是已知命令名才收成一个。
       （行分隔符 `\\` 后面跟 `&`/空白/行尾的，原样不动。） */
    var TEX_COMMANDS = /^(left|right|begin|end|frac|dfrac|tfrac|sqrt|sum|prod|int|iint|oint|cdot|cdots|ldots|dots|times|div|pm|mp|vec|hat|bar|tilde|dot|ddot|overline|underline|mathbf|mathrm|mathcal|mathbb|mathit|boldsymbol|text|textbf|textit|quad|qquad|lambda|alpha|beta|gamma|delta|Delta|Gamma|Lambda|Sigma|Omega|mu|nu|rho|sigma|tau|phi|varphi|psi|omega|theta|pi|det|dim|ker|rank|tr|to|mapsto|in|notin|subset|supset|cup|cap|varnothing|emptyset|partial|nabla|infty|approx|equiv|neq|leq|geq|le|ge|sim|propto|cases|pmatrix|bmatrix|vmatrix|array|leftrightarrow|Leftrightarrow|rightarrow|Rightarrow|xrightarrow|longrightarrow)/;
    function foldEscapes(body) {
      return String(body).replace(/(^|[^\\])\\\\([a-zA-Z]+)/g, function (whole, prefix, name) {
        return TEX_COMMANDS.test(name) ? prefix + "\\" + name : whole;
      });
    }
    function mathHtml(raw, sourceMode) {
      var display = raw.slice(0, 2) === "$$" || raw.slice(0, 2) === "\\[";   /* \\[…\\] 也是独立公式 */
      var body = foldEscapes(raw.replace(/^\$\$?/, "").replace(/\$\$?$/, "")
                                   .replace(/^\\[\[(]/, "").replace(/\\[\])]$/, ""));
      var html = "";
      if(studyMath)body=studyMath.operatorLimits(body);
      try {
        html = katex.renderToString(body, { displayMode: display, throwOnError: false, strict: false });
      } catch (error) {
        html = "";
      }
      /* ⚠️ `throwOnError:false` 时 KaTeX **不抛异常**，而是返回带 `.katex-error` 的 HTML，
         所以只靠 catch 兜底永远不触发，页面里会留下 `.katex-error`，浏览器验收随即失败
         （实测 run-20261011-125024：原书"习题参考答案"里一个 `\left[` 缺配对的 `\right]`
         把整章验收钉成 failed，而那段是**必须逐字保留、不许改写**的教材原文）。 */
      if (html && html.indexOf("katex-error") < 0) return html;
      /* 原文（sourceMode）：坏公式按原文保留、**不送进 KaTeX**，并留一个可见标注。
         页面里因此不再有 `.katex-error`，验收不会被原书自身的排版残缺挡住。 */
      if (sourceMode) {
        return '<code class="math-error source-formula-unrendered"'
          + ' title="原书此处的公式无法解析；按原文保留、未做渲染">'
          + escapeHtml(body) + "</code>";
      }
      /* 非原文（模型写的正文）：**必须保留 KaTeX 自己的 `.katex-error` 标记**，
         浏览器验收要照旧抓到它——那是质量闸门，不能被这段兜底悄悄放过去。 */
      if (html) return html;
      return '<code class="math-error">' + escapeHtml(body) + "</code>";
    }

    function refHtml(ref) {
      if (ref.type === "MEDIA") return '<div class="reader-media" data-mount-id="' + escapeHtml(ref.id) + '" data-media-anchor="' + escapeHtml(ref.id) + '"></div>';
      if (ref.type === "CONCEPT") {
        var concept = concepts[ref.id];
        var label = ref.display || (concept && concept.name) || ref.id;
        if (!concept) {
          return '<span class="missing-ref" title="概念表里没有这个 id">' + escapeHtml(label) + "</span>";
        }
        return '<a class="concept" href="#concept=' + encodeURIComponent(ref.id) +
          '" data-concept-id="' + escapeHtml(ref.id) + '">' + renderInline(label) + "</a>";
      }
      if (ref.type === "DIAGRAM") {
        return diagramHtml(ref.id);
      }
      if (ref.type === "MODEL") {
        return modelHtml(ref.id);
      }
      if (ref.type === "NOTE") {
        return noteHtml(ref.id);
      }
      if (ref.type === "EXPLAIN") {
        return explainHtml(ref.id, ref.display);
      }
      // 例题随正文保留标题入口；题干、思路、步骤和答案整体默认收起。
      // （2026-10-01 用户定案：例题本意就是随在正文里，不该堆到正文最后）
      if (ref.type === "EXAMPLE") {
        return exampleInlineHtml(ref.id);
      }
      return figureHtml(ref.id);
    }

    /** 例题的就地折叠入口（`[[EXAMPLE:E-01]]`）。
     *
     * 为什么是 `<span style="display:block">` 而不是 `<div>`：锚点常在一段话中间，
     * 插块级元素会把外层 `<p>` 提前闭合，例题会跑到段落外面去（和批注踩过同一个坑）。
     */
    function exampleInlineHtml(id) {
      // ⚠️ 单元上下文这个变量叫 **unitCtx**（beginUnit(ctx) 里赋的）；
      //    写成 state.examples 会当场 ReferenceError → 整段正文渲染失败
      //    （实测症状：锚点既没展开也没原样露出，DOM 里 rawAnchor = -1）。
      var list = (unitCtx && unitCtx.examples) || [];
      var item = null;
      for (var i = 0; i < list.length; i++) {
        if (list[i] && String(list[i].id) === String(id)) { item = list[i]; break; }
      }
      if (!item) return '<span class="example-missing">［例题 ' + escapeHtml(id) + ' 缺］</span>';
      var parts = ['<details class="example-inline reader-example-disclosure"><summary>' + renderInline(item.label || id) + '</summary>'];
      if (item.statement) {
        parts.push('<span class="exstatement">' + compactHtml(item.statement) + '</span>');
      }
      if (item.thought) {
        parts.push('<span class="exthought"><b>思路</b>：' + compactHtml(item.thought) + '</span>');
      }
      if (item.warmup) {
        parts.push('<span class="exwarm"><b>先用一个简单情形看明白</b>：' +
                   compactHtml(item.warmup) + '</span>');
      }
      var steps = item.steps || [];
      if (steps.length) {
        // ⚠️ **不能用 `<ol>/<li>`**：锚点在一段话中间，块级列表会被 HTML 解析器
        //    踢出外层 span（实测 steps=0、basis=0，卡片里只剩标题和 warmup）。
        //    改用行内 span + CSS 的 display:block，视觉上仍是"一步一行"。
        parts.push('<span class="exsteps"><b>一步一步解</b>（' + steps.length + ' 步）</span>');
        steps.forEach(function (step, i) {
          var basis = step.basis
            ? '<span class="basis">依据：' + compactHtml(step.basis) + '</span>' : "";
          parts.push('<span class="exstep">' + (i + 1) + '. ' +
                     compactHtml(step.markdown || "") + basis + '</span>');
        });
      }
      if (item.conclusion) {
        parts.push('<span class="exconcl"><b>结论</b>：' + compactHtml(item.conclusion) + '</span>');
      }
      parts.push('</details>');
      return parts.join('');
    }

    /** 批注内容里可能又有段落、又有公式：把 `<p>` 换成 display:block 的 `<span>`。
     *
     * 为什么不能直接用 renderMarkdown：批注标记在**段落中间**，如果插进去的是块级
     * `<p>`，浏览器解析时会把外层那个 `<p>` 提前闭合 —— 批注会跑到段落外面去
     * （表现为"批注内容和它要解释的那句话分了家"，正好破坏"能指向"）。
     * `<span>` 是行内元素（合法地待在 `<p>` 里），再用 CSS 让它按块显示，两头都顾到。 */
    function compactHtml(markdown) {
      popDepth += 1;
      var html;
      try {
        html = renderMarkdown(String(markdown == null ? "" : markdown));
      } finally {
        popDepth -= 1;
      }
      return html
        .replace(/<\/p>\s*<p[^>]*>/g, '<span class="pp-sep"></span>')
        .replace(/<p[^>]*>/g, '<span class="pp">')
        .replace(/<\/p>/g, "</span>");
    }

    /** 正文里那个"带编号的小圆点" + 紧跟其后的就地展开批注。 */
    function noteHtml(id) {
      var note = unitCtx && unitCtx.notes ? unitCtx.notes[id] : null;
      if (!note) {
        // 没有上下文（node 侧单独渲染一段文本时就是这样）→ 退回老样子：跳转到批注区
        return '<a class="note-badge" href="#note-' + encodeURIComponent(id) +
          '" data-note-id="' + escapeHtml(id) + '" title="看批注">注</a>';
      }
      if (unitCtx.noteOrder.indexOf(id) < 0) unitCtx.noteOrder.push(id);
      var ordinal = unitCtx.noteOrder.indexOf(id) + 1;
      var kind = note.kind || "note";
      if (popDepth > 0) return '<span class="mk-inline">' + ordinal + "</span>";
      var head = '<span class="chip">' + escapeHtml(NOTE_KIND[kind] || kind) + "</span>";
      if (note.title) head += '<b class="ptitle">' + renderInline(note.title) + "</b>";
      // **小字链接**（2026-10-02 用户定案）：原来是个编号圆点，读者不知道那是什么；
      // 现在显示批注**类型**（直觉/易错/推导/注）的小字，点它就地展开全文。
      var _label = NOTE_KIND[kind] || "注";
      return '<button class="mk mk-' + escapeHtml(kind) + '" type="button" data-note="' +
        escapeHtml(id) + '" aria-expanded="false" title="' +
        // 原生 title 只认纯文本 → 先捋掉 LaTeX（P-045；不然悬停出来是 `$j_2$ 的两个条件`）
        escapeHtml(plainMath(note.title || NOTE_KIND[kind] || "批注")) + '">' + _label + "</button>" +
        '<span class="pop pop-' + escapeHtml(kind) + '" data-note="' + escapeHtml(id) + '" hidden>' +
        head + '<span class="nb">' + compactHtml(note.markdown) + "</span></span>";
    }

    /** 公式 / 定理的"详细解释"入口（P-006 第二条铁律）。
     *
     * 教案里写 `[[EXPLAIN:F-01|均值不等式]]`，这里展开成一个可点的小胶囊：
     * 点开就有**它是什么** + **为什么在这里用它**（用户要的就是这两句），
     * 详解栏里同时出现同号卡片，长推导可以摊开看。 */
    function explainHtml(id, display) {
      var item = unitCtx && unitCtx.explain ? unitCtx.explain[id] : null;
      var label = display || (item && item.name) || id;
      if (!item) {
        return '<span class="missing-ref" title="详解表里没有这一条">' +
          escapeHtml(label) + "</span>";
      }
      if (unitCtx.explainOrder.indexOf(id) < 0) unitCtx.explainOrder.push(id);
      if (popDepth > 0) return '<span class="mk-inline">' + renderInline(label) + "</span>";
      var kind = item.kind === "theorem" ? "定理" : "公式";
      var body = "";
      if (item.what) body += '<span class="pp"><b>它是什么：</b>' + renderInline(item.what) + "</span>";
      if (item.why) body += '<span class="pp why"><b>为什么在这里用它：</b>' + renderInline(item.why) + "</span>";
      var proof = item.markdown ? compactHtml(item.markdown) : "";
      (item.steps || item.walkthrough || []).forEach(function (step) {
        proof += '<span class="pp">' + compactHtml(step.markdown || step.claim || "") +
          (step.basis ? '<span class="basis">依据：' + compactHtml(step.basis) + '</span>' : '') + '</span>';
      });
      body += item.kind === 'theorem' && proof ? '<span data-reader-legacy-proof="">' + proof + '</span>' : proof;
      return '<button class="fchip" type="button" data-explain="' + escapeHtml(id) +
        '" aria-expanded="false">' + renderInline(label) + '<span class="fq">?</span></button>' +
        '<span class="pop pop-explain" data-explain="' + escapeHtml(id) + '" hidden>' +
        '<span class="chip">' + kind + "</span>" +
        '<b class="ptitle">' + renderInline(item.name || label) + "</b>" +
        '<span class="nb">' + body + "</span></span>";
    }

    /** 模型：只放一个容器，真正的渲染交给 models.js（它在 DOM 挂好之后跑）。
     *
     * 为什么分两步：画布要 WebGL、要量尺寸，必须等元素进了文档再画；
     * 而这一段是**纯字符串拼接**（能在 node 里测），所以这里只留挂载点。
     */
    function modelHtml(modelId) {
      var spec = models[modelId];
      if (!spec) {
        return '<div class="model missing-ref">［模型 ' + escapeHtml(modelId) + ' 找不到规格］</div>';
      }
      var kind = spec.kind === "symbolic" ? "symbolic" : "canvas";
      /* P-003：模型卡片上要能一眼看出"**它替教材做哪一步**"（用户原话：建模相关性不行）。
         `why_here` 就是模型自己写的那句话；没有它就不要摆这个模型（校验器 R17 会记一条）。
         三维还要 `view_hint`（往哪看、先看什么）—— 不然就是"能拖的装饰"。 */
      var head = '<div class="model-head">' +
        (spec.title ? '<h4 class="model-title">' + escapeHtml(spec.title) + "</h4>" : "") +
        (spec.why_here ? '<span class="model-bind">它替教材做这一步：' +
          escapeHtml(spec.why_here) + "</span>" : "") + "</div>";
      if (spec.diagnostic_unavailable) {
        return '<figure class="model model-failed" data-model-id="' + escapeHtml(modelId) + '">' +
          head + '<p class="model-fallback">' + escapeHtml(spec.diagnostic_unavailable) + '</p></figure>';
      }
      return '<figure class="model model-' + kind + '" data-model-id="' + escapeHtml(modelId) + '">' +
        head + '<div class="model-stage"></div>' +
        (spec.view_hint ? '<p class="model-viewhint">怎么看：' + escapeHtml(spec.view_hint) + "</p>" : "") +
        '<div class="model-ui"></div>' +
        '<figcaption class="model-caption">' + escapeHtml(spec.caption || "") + "</figcaption></figure>";
    }

    /** 正文：按"块"渲染，顺手把定义/定理这类骨架包成卡片（排版重构：重点要跳出来）。
     *
     * 为什么要分块：markdown 里没有"这是定义"这种语义，但教材正文里**第一句就是**
     * 「定义 2.1.1 …」—— 这是可机械识别的（也正因为可机械识别，才敢在渲染层做，
     * 而不用让模型多写标记）。分块渲染还有一个好处：一块坏了不影响别块。
     */
    var CARD_RULES = [
      { cls: "def", tag: "定义", re: /^\s*(?:\*\*)?定义(?:\*\*)?\s*[0-9０-９]*\s*[．.、]?/ },
      { cls: "thm", tag: "定理", re: /^\s*(?:\*\*)?定理(?:\*\*)?\s*[0-9０-９]*\s*[．.、]?/ },
      { cls: "prop", tag: "命题", re: /^\s*(?:\*\*)?命题(?:\*\*)?\s*[0-9０-９]*\s*[．.、]?/ },
      { cls: "cor", tag: "推论", re: /^\s*(?:\*\*)?推论(?:\*\*)?\s*[0-9０-９]*\s*[．.、]?/ },
      { cls: "concl", tag: "结论", re: /^\s*(?:\*\*)?结论(?:\*\*)?\s*[0-9０-９]*\s*[．.、]?/ },
      { cls: "warn", tag: "易错", re: /^\s*(?:\*\*)?易错(?:点)?(?:\*\*)?\s*[0-9０-９]*\s*[．.、]?/ }
    ];
    function cardOf(block) {
      var head = String(block || "").slice(0, 40);
      for (var i = 0; i < CARD_RULES.length; i++) {
        if (CARD_RULES[i].re.test(head)) {
          var num = (head.match(/[0-9０-９]+(?:\s*[.．]\s*[0-9０-９]+)*/) || [""])[0];
          return { cls: CARD_RULES[i].cls, tag: CARD_RULES[i].tag + (num ? " " + num : "") };
        }
      }
      return null;
    }
    function renderBody(markdown) {
      var blocks = String(markdown == null ? "" : markdown).split(/\n{2,}/);
      var out = [];
      blocks.forEach(function (block) {
        if (!block.trim()) return;
        var card = cardOf(block);
        var html = renderMarkdown(block);
        out.push(card
          ? '<div class="card card-' + card.cls + '"><span class="card-tag">' + escapeHtml(card.tag) +
            "</span>" + html + "</div>"
          : html);
      });
      return out.join("\n");
    }

    /** 过程图：把"一步步的过程"画出来（用户要求："一步步推导最好有示意图"）。
     *
     * 模型只给**结构化数据**（一串矩阵 + 每步操作），这里按模板确定性渲染 ——
     * 画法属于渲染层，数据属于教案（G2 数据与渲染分离）。
     */
    function diagramHtml(diagramId) {
      var spec = diagrams[diagramId];
      if (!spec) {
        return '<span class="missing-ref" title="找不到这张过程图">［过程图 ' +
          escapeHtml(diagramId) + "］</span>";
      }
      var steps = (spec.steps || []).filter(function (s) {
        return (s && (s.math || s.label || "")).toString().trim().length > 0;
      });
      var title = spec.title
        ? "<figcaption>" + renderInline(spec.title) + "</figcaption>" : "";
      // ── 新模板（2026-09-30，P4）：只加"能画准"的 ────────────────────────
      // 数轴：一条线 + 若干带标签的点（区间、根、符号分析用）
      if (spec.template === "number-line") {
        var marks = steps.map(function (step) {
          return '<span class="nl-point">' + renderInline(step.label || step.math || "") +
            (step.note ? '<span class="nl-note">' + renderInline(step.note) + "</span>" : "") +
            "</span>";
        }).join('<span class="nl-seg"></span>');
        return '<figure class="diagram number-line" data-diagram-id="' +
          escapeHtml(diagramId) + '"><div class="nl-track">' + marks + "</div>" + title + "</figure>";
      }
      // 依赖图 / 电路拓扑：元件或概念串成一行，箭头带连接说明
      if (spec.template === "dependency" || spec.template === "circuit") {
        var chain = steps.map(function (step, index) {
          var node = '<span class="node' + (spec.template === "circuit" ? " comp" : "") + '">' +
            renderInline(step.label || step.math || "") + "</span>";
          var op = String(step.op || "").trim();
          var arrow = index < steps.length - 1
            ? '<span class="arrow">' + (op ? renderInline(op) + " " : "") + "→</span>" : "";
          return node + arrow;
        }).join("");
        return '<figure class="diagram ' + spec.template + '" data-diagram-id="' +
          escapeHtml(diagramId) + '"><div class="dep-row">' + chain + "</div>" + title + "</figure>";
      }
      var kind = spec.template === "process" ? " process" : " matrix-flow";
      var inner = steps.map(function (step, index) {
        if (spec.template === "process") {
          var node = '<span class="node">' + renderInline(step.label || "") + "</span>";
          var arrow = index < steps.length - 1
            ? '<span class="arrow">→</span>' : "";
          return node + arrow;
        }
        // matrix-flow：一步一块矩阵，块之间是"操作 + 箭头"
        var math = String(step.math || "");
        if (math.slice(0, 2) !== "$$") {
          math = "$$" + math.replace(/^\$+/, "").replace(/\$+$/, "") + "$$";
        }
        var cell = '<div class="diagram-step">' + mathHtml(math) +
          (step.note ? '<div class="step-note">' + renderInline(step.note) + "</div>" : "") +
          "</div>";
        /* 箭头上的操作（`r_2-2r_1`、`(1)\\leftrightarrow(2)`）本身就是公式：
           模型写它时**不带 `$`**，不补定界符就会把 LaTeX 原样露在页面上（实测就是这个）。
           已经是 `$…$` 的就别重复包一层。 */
        var op = String(step.op || "").trim();
        var opHtml = "";
        if (op) {
          if (op.charAt(0) !== "$") op = "$" + op.replace(/^\$+/, "").replace(/\$+$/, "") + "$";
          opHtml = '<span class="op">' + renderInline(op) + "</span>";
        }
        var link = index < steps.length - 1
          ? '<div class="diagram-arrow">' + opHtml + '<span class="head">→</span></div>'
          : "";
        return cell + link;
      }).join("");
      return '<figure class="diagram' + kind + '" data-diagram-id="' +
        escapeHtml(diagramId) + '">' + inner + title + "</figure>";
    }

      /** 例题：一步步解 + 每一步的"依据"（P-006 第四条铁律：例题推导同样要有依据、能点开看）。
     *
     * 步骤的 markdown 里可以直接写 [[EXPLAIN:F-02]]（公式/定理详解）与 [[CONCEPT:…]]；
     * 另外允许一步带 `basis` 字段（一句人话的依据）—— 渲染成一个可点的「这一步的依据」。 */
    function examplesHtml(examples) {
      var out = ["<section class='examples'><h2>例题</h2>"];
      examples.forEach(function (item, exIndex) {
        out.push("<details class='example reader-example-disclosure' data-example-label='" + escapeHtml(item.label) + "'>");
        out.push("<summary>" + renderInline(item.label || "例题") + "</summary>");
        out.push("<div class='markdown statement'>" + renderMarkdown(item.statement || "") + "</div>");
        if (item.thought) {
          out.push("<p class='thought'><b>思路</b>：" + renderInline(item.thought) + "</p>");
        }
        // **先简后繁**（2026-09-30 P3）：复杂题先给一个极简特例，再进正题
        if (item.warmup) {
          out.push("<div class='warmup'><b>先用一个简单情形看明白</b>：" +
                   renderInline(item.warmup) + "</div>");
        }
        /* 例题要的图**必须在页面上现身**（2026-10-01）：模型在 `figure_requests` 里写清了
           "要哪张图、为什么需要、用在哪一步"，原来谁也不显示 —— 读者只看到干巴巴的推导，
           连"这里本该有一张电路图"都不知道。纯数据驱动，**任何书**都生效。 */
        // 例题**已经配到图**就把它画出来（`figures[]` 由教案层按图请求确定性挂上）
        (item.figures || []).forEach(function (ref) {
          out.push(figureHtml(typeof ref === "string" ? ref : (ref && ref.id)));
        });
        var pendingFigures = item.pending_figure_requests || item.figure_requests || [];
        if (pendingFigures.length) {
          out.push("<div class='figreq'><b>这一题需要一张图</b>（尚未画出）：<ul>");
          pendingFigures.forEach(function (req) {
            out.push("<li>" + renderInline(req.what || "") +
              (req.why ? "<div class='why'>为什么需要：" + renderInline(req.why) + "</div>" : "") +
              (req.where ? "<div class='where'>用在哪：" + renderInline(req.where) + "</div>" : "") +
              "</li>");
          });
          out.push("</ul></div>");
        }
        out.push("<div class='steps'><div class='steps-head'>一步一步解（" +
                 (item.steps || []).length + " 步）</div><ol>");
        (item.steps || []).forEach(function (step, stepIndex) {
          out.push("<li><div class='markdown'>" + renderMarkdown(step.markdown || "") + "</div>");
          if (step.basis) {
            var bid = "B-" + exIndex + "-" + (stepIndex + 1);
            out.push("<button class='basis' type='button' data-basis='" + escapeHtml(bid) +
                     "' aria-expanded='false'>这一步的依据</button>" +
                     "<span class='pop pop-basis' data-basis='" + escapeHtml(bid) +
                     "' hidden><span class='chip'>依据</span><span class='nb'>" +
                     renderInline(step.basis) + "</span></span>");
          }
          out.push("</li>");
        });
        out.push("</ol></div>");
        if (item.conclusion) {
          out.push("<div class='markdown conclusion'><b>结论</b>：" + renderMarkdown(item.conclusion) + "</div>");
        }
        if (item.key_points && item.key_points.length) {
          out.push("<ul class='key-points'><li><b>要点</b>：" +
                   item.key_points.map(function (x) { return renderInline(x); }).join("；") +
                   "</li></ul>");
        }
        if (item.pitfalls && item.pitfalls.length) {
          out.push("<details class='pitfall'><summary>易错点（" + item.pitfalls.length +
                   " 条）</summary><ul>" + item.pitfalls.map(function (x) {
                     return "<li>" + renderInline(x) + "</li>"; }).join("") + "</ul></details>");
        }
        out.push("</details>");
      });
      out.push("</section>");
      return out.join("");
    }

    /** 一张图的 HTML：**生成的图内联 SVG**（颜色跟主题走），扫描裁图沿用 <img>。 */
    function figureHtml(figureId, readerMediaOptions) {
      var figure = figures[figureId];
      if (!figure || (!figure.file && !figure.svg)) {
        return '<span class="missing-ref" title="找不到这张图">［插图 ' +
          escapeHtml(figureId) + "］</span>";
      }
      var caption = readerMediaOptions && readerMediaOptions.caption !== undefined ? readerMediaOptions.caption : figure.caption || "";
      var alt = readerMediaOptions && readerMediaOptions.alt_text || figure.alt_text || caption;
      var kind = figure.source === "generated" || figure.svg ? " generated" : "";
      var body;
      if (figure.scene) {
        /* 3D 图：给一个**画布** + 把**场景描述**放 data 属性里（页面端 three.js 照它画）。
           为什么把场景塞进属性而不是让页面去取文件：页面是静态的、离线自包含的，
           取文件会多一次请求、也会破坏"单文件"的约定。
           静态贴片留着做**兜底**：WebGL 不可用/初始化失败时显示它，而不是留一块空白。 */
        body = '<div class="fig3d">' +
          '<canvas class="fig3d-canvas" data-scene="' +
          escapeHtml(JSON.stringify(figure.scene)) + '" width="520" height="360"></canvas>' +
          '<div class="fig3d-fallback">' + (figure.svg || "") +
          '<div class="missing">（这台设备没能启用 3D，先看静态贴片）</div></div>' +
          '<div class="missing fig3d-hint">按住拖动可以转视角</div></div>';
      } else {
        body = figure.svg
          ? figure.svg                                  // 内联：currentColor 才能跟随主题
          : '<img loading="lazy" src="' + escapeHtml(figure.file) + '" alt="' +
            escapeHtml(alt) + '">';
      }
      var captionId = 'caption-' + String(readerMediaOptions && readerMediaOptions.mount_id || figureId).replace(/[^a-zA-Z0-9_-]/g,'-');
      if (figure.svg) body = body.replace(/<svg\b([^>]*)>/, function (_, attrs) {
        return '<svg' + attrs.replace(/\s(?:role|aria-label|aria-labelledby)\s*=\s*(?:"[^"]*"|'[^']*')/g,'') +
          ' role="img" aria-label="' + escapeHtml(alt) + '">';
      });
      return '<figure class="figure' + kind + '" data-figure-id="' +
        escapeHtml(figureId) + '"' + (caption ? ' aria-describedby="'+escapeHtml(captionId)+'"' : '') + '><div class="media-viewport">' + body + '</div>' +
        (caption ? '<figcaption class="media-details" id="'+escapeHtml(captionId)+'">' + (readerMediaOptions ? renderReaderMarkdown(caption,true) : renderInline(caption)) + "</figcaption>" : "") +
        "</figure>";
    }

    function expand(html, store) {
      var out = html.replace(new RegExp(SLOT + "(\\d+)" + SLOT, "g"), function (all, index) {
        var item = store[Number(index)];
        if (!item) return all;
        return item.kind === "math" ? mathHtml(item.raw, item.sourceMode) : refHtml(item);
      });
      // 独占一段的插图不要被 <p> 包着（否则会是行内块，排版很怪）
      out = out.replace(/<p>\s*(<figure[\s\S]*?<\/figure>)\s*<\/p>/g, "$1");
      return out;
    }

    /** 行内渲染：适用于 brief / formal / 名称 / 图注这类**短字段**。
     *
     * 踩过的坑：这些字段一开始是用 escapeHtml 直接输出的，于是里面的
     * `$\boldsymbol{a}$` 就**原样显示成美元符加反斜杠**——"定义的简单概念"
     * 最容易中招（它们的内容几乎全在 brief 和 formal 里）。
     * 这里转义 HTML（安全），但公式照常交给 KaTeX。
     */
    function renderInline(text) {
      var store = [];
      var masked = maskBareMath(mask(text, store, concepts), store);
      return expand(escapeHtml(masked), store);
    }

    /** markdown → HTML（公式/引用已由 mask 收进仓库）。
     *
     * `numbers` 决定这一处的编号怎么处理：
     *   · `"link"`   —— 正文/例题：`(2.1)` 做成能点的链接（跳到原文对照，P-035）
     *   · `"anchor"` —— 原文（对照用）：给 `(2.1)` 打锚点，让上面那些链接跳得进来
     *   · 空         —— 原样不动
     * 为什么在 `marked.parse` **之前**做：那时公式与 `[[…]]` 都还在仓库里，
     * 替换只可能落在散文字符上 —— 碰不到 KaTeX 产物，也碰不到 HTML 属性。 */
    function bareTokens(tokens,store){(tokens||[]).forEach(function(token){
      if(/^(?:code|codespan|html|image)$/.test(token.type))return;
      if(token.tokens)bareTokens(token.tokens,store);
      else if(token.type==='text'&&katex){token.text=maskBareMath(token.text.replace(/&gt;/g,'>').replace(/&lt;/g,'<'),store).replace(/</g,'&lt;').replace(/>/g,'&gt;');token.raw=token.text;}
      if(token.items)token.items.forEach(function(item){bareTokens(item.tokens,store);});
      if(token.header)bareTokens(token.header,store);
      if(token.rows)token.rows.forEach(function(row){bareTokens(row,store);});
    });}
    function markdownToHtml(text, numbers) {
      var store = [];
      var masked = mask(text, store, concepts);
      // R3: bare TeX inside markdown link text renders like the rest of the label.
      masked = masked.replace(/\[([^\]\n]+)\]\(/g, function (_all, label) {
        return "[" + maskBareMath(label, store) + "](";
      });
      if (numbers === "link") {
        masked = linkNumbers(masked, deps.numberRefs, unitCtx && unitCtx.unitId);
      } else if (numbers === "anchor") {
        masked = anchorNumbers(masked);
      }
      var html;
      if(marked){var tokens=marked.lexer(masked,{mangle:false,headerIds:false});bareTokens(tokens,store);html=marked.parser(tokens,{mangle:false,headerIds:false});}
      else html="<p>"+escapeHtml(masked)+"</p>";
      return expand(html, store);
    }

    function renderMarkdown(text) {
      return markdownToHtml(text, deps.numberRefs ? "link" : "");
    }

    /** 原文（对照用）：编号打锚点（P-035）。正文那侧是链接，原文这侧是落点。 */
    function renderOriginal(text) {
      return markdownToHtml(text, "anchor");
    }

    /** 讲解里出现的概念 id（按出现顺序，去重）—— 用来做"本页涉及的概念"列表 */
    function referencedConcepts(text) {
      var store = [];
      mask(text, store, concepts);
      var ids = [];
      store.forEach(function (item) {
        if (item.kind === "ref" && item.type === "CONCEPT" && ids.indexOf(item.id) < 0) {
          ids.push(item.id);
        }
      });
      return ids;
    }

    // Reader prose is data: Markdown owns structure, raw HTML is literal text,
    // and mathematical spans alone reach KaTeX. No mutable concept table is read.
    function renderReaderMarkdown(text, inline, sourceMode) {
      var store = [], masked = text;
      sliceSpans(masked).reverse().forEach(function (span) {
        var slot = SLOT + store.length + SLOT;
        store.push({kind: "math", raw: span.raw, sourceMode: !!sourceMode});
        masked = masked.slice(0, span.start) + slot + masked.slice(span.end);
      });
      if (!marked) return expand(escapeHtml(masked), store);
      var renderer = new marked.Renderer();
      renderer.html = function (raw) { return escapeHtml(raw); };
      renderer.link = function (href, title, text) {
        // Reference links are host-generated. Other URLs remain visible data
        // unless they use an ordinary web scheme; arbitrary schemes never run.
        if (!/^(?:https?:\/\/|#reader-[a-f0-9]{20}$)/i.test(href)) {
          return text + " (" + escapeHtml(href) + ")";
        }
        return '<a href="' + escapeHtml(href) + '">' + text + '</a>';
      };
      renderer.image = function (href, title, text) {
        var asset = sourceMode && (deps.sourceAssets || {})[href];
        if (asset && /^assets\/source\/[a-f0-9]{64}\.(png|jpe?g|webp|gif)$/.test(asset)) {
          return '<img class="source-image" loading="lazy" src="'+escapeHtml(asset)+'" alt="'+escapeHtml(text || '')+'">';
        }
        // Media is separately approved; author prose cannot fetch/embed assets.
        return escapeHtml(text) + " (" + escapeHtml(href) + ")";
      };
      var options = {renderer: renderer, mangle: false, headerIds: false};
      var html;
      if(inline)html=marked.parseInline(masked,options);
      else {var tokens=marked.lexer(masked,options);bareTokens(tokens,store);html=marked.parser(tokens,options);}
      return expand(html, store);
    }

    function sourceLocation(source) {
      var lines = source.source_lines, pages = source.source_pages || [];
      var parts = ['原书资料，仅记录'];
      if (lines && lines.length === 2) parts.push('来源行 '+lines[0]+'–'+lines[1]);
      if (pages.length) parts.push('PDF 页 '+pages.join('、'));
      else if ((source.source_hint || {}).pdf_page) parts.push('目录标注 PDF 页 '+source.source_hint.pdf_page);
      return '<p class="source-location">'+escapeHtml(parts.join(' · '))+'</p>';
    }

    function renderSourceMaterial(source) {
      var formatting = source.formatting || {};
      var formatted = formatting.status === 'formatted' && typeof source.display_markdown === 'string' && source.display_markdown.trim();
      var markdown = formatted ? source.display_markdown : source.raw_markdown || '';
      var notes = formatted && Array.isArray(formatting.notes) ? formatting.notes.filter(function(note) {
        return typeof note === 'string' && note.trim();
      }).slice(0, 3) : [];
      return '<article class="unit source-material"><header class="unit-head"><h1>'+escapeHtml(source.title || '习题资料')+'</h1></header>'+sourceLocation(source)+
        (source.study_pdf_url ? '<p><a href="'+escapeHtml(source.study_pdf_url)+'" target="_blank" rel="noopener">打开教材参考答案原页（PDF）</a></p>' : '')+
        (source.state === 'unresolved' ? '<p role="status">'+escapeHtml(source.reason || '来源位置尚未定位')+'</p>' : '')+
        (formatted ? '<p class="source-location source-format-status">模型仅整理格式，未核实答案正确性。</p>' : '')+
        (notes.length ? '<details class="source-format-notes"><summary>文字整理记录</summary><div class="markdown">'+renderReaderMarkdown(notes.join('\n\n'),false,true)+'</div></details>' : '')+
        '<div class="markdown">'+renderReaderMarkdown(markdown, false, true)+'</div>'+
        (formatted ? '<details class="source-original"><summary>查看原文</summary><div class="markdown">'+
          renderReaderMarkdown(source.study_raw_display || source.raw_markdown || '', false, true)+'</div></details>' : '')+'</article>';
    }

    var readerMedia = [], readerLayout = null;
    function readerMountHtml(mount) {
      var item = readerMedia.filter(function(row){return row.request_id === mount.source_id;})[0];
      var out = '<div class="reader-media" data-mount-id="'+escapeHtml(mount.mount_id)+'" data-media-anchor="'+escapeHtml(mount.anchor_id)+'"'+
        ' data-media-request="'+escapeHtml(mount.source_id)+'" data-reader-owner="'+escapeHtml(mount.owner_id)+'"'+
        ' data-source-id="'+escapeHtml(mount.source_id)+'" data-source-kind="'+escapeHtml(mount.source_kind)+'" data-source-hash="'+escapeHtml(mount.source_hash)+'" data-unit-id="'+escapeHtml(mount.unit_id)+'">';
      if (mount.source_kind === 'interaction') {
        var modelId = mount.model_id || mount.source_id;
        out += modelHtml(modelId);
      } else if (item && item.figure_id) out += figureHtml(item.figure_id,Object.assign({},item,{mount_id:mount.mount_id}));
      // Uncertainty and reviewer notes remain in the data/report, outside the
      // teaching page. Keep genuine missing/error notices where media belongs.
      if (item && item.diagnostic_status !== undefined && item.diagnostic_status !== 'unverified') {
        if (!item.figure_id && (item.kind !== 'animation' || item.diagnostic_status === 'missing' || item.diagnostic_status === 'known_error')) out += '<div class="media-viewport reader-media-missing" role="status">媒体资源缺失</div>';
        var statusTitle = {verified:'已核实',known_error:'确定错误，暂不展示',missing:'媒体资源缺失'}[item.diagnostic_status] || item.diagnostic_status;
        out += '<div class="media-details reader-media-diagnostic" data-reader-state="'+escapeHtml(item.diagnostic_status)+'" role="status">'+escapeHtml(statusTitle)+
          (item.diagnostic_reason ? '：'+escapeHtml(item.diagnostic_reason) : '')+'</div>';
      }
      return out+'</div>';
    }
    var readerPresentation = null;
    function readerBlockHtml(block, extra) {
      if (block.raw_source) {
        return '<div class="reader-block markdown source-material" data-reader-pointer="'+escapeHtml(block.json_pointer)+'" data-reader-block="'+escapeHtml(block.block_id)+'">'+
          (block.json_pointer.endsWith('/raw_markdown') ? sourceLocation(block) : '')+
          renderReaderMarkdown(block.markdown || '', false, true)+'</div>';
      }
      var tag = block.group === "title" ? "h1" : "div";
      var step = block.step_id ? ' data-reader-step="' + escapeHtml(block.step_id) +
        '" data-reader-step-no="' + escapeHtml(block.step_no) + '"' : "";
      if (!readerMarkdown) throw new Error('Shared Reader Markdown parser required');
      var parsed = readerMarkdown.parseReaderField(block.markdown,{marked:marked,field:block});
      var mounts = (readerLayout ? readerLayout.mounts : []).filter(function(m){return m.target_pointer===block.json_pointer;});
      var before = [], after = [];
      // A heading owns inline prose only; media slots are siblings in its group.
      if (tag === 'h1') {
        mounts.forEach(function(m){(m.edge==='before'?before:after).push(readerMountHtml(m));});
        mounts=[];
      }
      var content = readerMarkdown.renderReaderField(parsed,mounts,readerMountHtml,{inline:tag==='h1',bareMath:!!katex,mathHtml:mathHtml,teachingRoles:(readerPresentation||{}).roles,disclosureRole:block.group==='examples'?'worked_example':''});
      return before.join('')+'<' + tag + ' class="reader-block markdown" data-reader-pointer="' + escapeHtml(block.json_pointer) +
        '" data-reader-block="' + escapeHtml(block.block_id) + '"' + step + (extra || "") + '>' +
        (block.group === "title" ? "" : '<span class="reader-field-label">' + escapeHtml(block.field_label) + '</span>') +
        content + '</' + tag + '>'+after.join('');
    }

    function learningCheckHtml(blocks, check) {
      var out = ['<details class="reader-group learning-check" data-reader-group="learning_checks" data-reader-owner="' + escapeHtml(check.id) + '" id="' + escapeHtml(blocks[0].anchor) +
        '" data-learning-check="' + escapeHtml(check.id) + '" data-check-config="' + escapeHtml(JSON.stringify(check)) + '">',
        '<summary>随文自测 · 派生练习</summary>'];
      var hintBlocks = [], answerBlocks = [], feedbackBlocks = [], optionBlocks = [];
      blocks.forEach(function (block) {
        var p = block.json_pointer;
        if (/\/hints\//.test(p)) hintBlocks.push(block);
        else if (/\/answer$/.test(p)) answerBlocks.push(block);
        else if (/\/misconception_feedback\//.test(p)) feedbackBlocks.push(block);
        else if (/\/input_spec\/options\//.test(p)) optionBlocks.push(block);
        else out.push(readerBlockHtml(block));
      });
      if (check.kind === "choice" || check.kind === "boolean") {
        out.push('<fieldset style="min-width:0"><legend>选择你的答案</legend>');
        if (check.kind === "choice") {
          check.input_spec.options.forEach(function (option, i) {
            out.push('<label style="display:block;overflow-wrap:anywhere"><input type="radio" data-check-input name="' + escapeHtml(blocks[0].anchor) +
              '" value="' + escapeHtml(option.id) + '">' + readerBlockHtml(optionBlocks[i]) + '</label>');
          });
        } else {
          out.push('<label><input type="radio" data-check-input name="' + escapeHtml(blocks[0].anchor) + '" value="true">正确</label> ' +
            '<label><input type="radio" data-check-input name="' + escapeHtml(blocks[0].anchor) + '" value="false">错误</label>');
        }
        out.push('</fieldset>');
      } else {
        var label = check.kind === "numeric" ? "输入数值" : check.kind === "self_explanation" ? "写下你的理由（自行对照）" : "补出关键一步";
        out.push('<label style="display:block">' + label + '<textarea data-check-input rows="2" style="display:block;width:100%;max-width:100%;box-sizing:border-box"' +
          (check.kind === "numeric" ? ' inputmode="decimal"' : '') + '></textarea></label>');
      }
      out.push('<button type="button" data-check-action="submit">' + (check.kind === "self_explanation" ? "完成作答并自评" : "检查答案") + '</button> ' +
        '<button type="button" data-check-action="hint" disabled>查看下一条提示</button>',
        '<div data-check-result role="status" aria-live="polite"></div>');
      feedbackBlocks.forEach(function (block, i) {
        out.push(readerBlockHtml(block, ' data-check-feedback="' + escapeHtml(check.misconception_feedback[i].id) + '" hidden'));
      });
      hintBlocks.forEach(function (block, i) { out.push(readerBlockHtml(block, ' data-check-hint="' + i + '" hidden')); });
      out.push('<details data-check-solution hidden><summary>查看解法' + (check.kind === "self_explanation" ? '与自评依据' : '') + '</summary>');
      answerBlocks.forEach(function (block) { out.push(readerBlockHtml(block)); });
      out.push('</details></details>');
      return out.join("");
    }

    function gradeLearningCheck(check, input) {
      function normalize(value) {
        if (check.kind === "boolean") return typeof value === "boolean" ? {ok:true,value:value} : {ok:false};
        if (check.kind === "numeric") {
          if (typeof value === "number") return {ok:Number.isFinite(value),value:value};
          if (typeof value !== "string" || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim())) return {ok:false};
          var number = Number(value.trim()); return {ok:Number.isFinite(number),value:number};
        }
        if (typeof value !== "string" || !value.trim()) return {ok:false};
        if (check.kind === "choice") return {ok:check.input_spec.options.some(function (x) { return x.id === value; }),value:value};
        if (check.scoring.normalization !== "exact") value = value.trim();
        if (check.scoring.normalization === "casefold") value = Array.from(value).map(function (char) {
          return Object.prototype.hasOwnProperty.call(check.casefold_map,char) ? check.casefold_map[char] : char;
        }).join("");
        return {ok:true,value:value};
      }
      var given = normalize(input);
      if (!given.ok) return {status:"invalid",feedback_id:"",feedback:"请先输入有效答案。"};
      if (check.kind === "self_explanation") return {status:"manual",feedback_id:"",feedback:"请对照解法核对自己的理由；此题由你自评。"};
      function matches(expected, tolerance) {
        var other = normalize(expected); if (!other.ok) return false;
        if (check.kind === "numeric" && tolerance) return Math.abs(given.value-other.value) <=
          Math.max(check.scoring.absolute_tolerance,check.scoring.relative_tolerance*Math.abs(other.value));
        return given.value === other.value;
      }
      if (check.scoring.accepted_inputs.some(function (x) { return matches(x,true); })) return {status:"correct",feedback_id:"",feedback:"回答正确。请再核对使用条件。"};
      var branches = check.misconception_feedback;
      for (var i=0;i<branches.length;i++) {
        if (branches[i].inputs.some(function (x) { return matches(x,false); })) return {status:"incorrect",feedback_id:branches[i].id,feedback:""};
      }
      return {status:"incorrect",feedback_id:"",feedback:"结果尚未匹配。检查条件与关键一步，可逐级查看提示。"};
    }

    function readerSupportReference(block) {
      var match = String(block.markdown || '').match(/^\s*\[(?:\\.|[^\]\\\n])*\]\(#(reader-[A-Za-z0-9_-]+)\)[。.!！]?\s*$/);
      return match ? match[1] : '';
    }
    function readerSupportTarget(block) {
      var item = (block.json_pointer || '').match(/^\/(?:examples|explainables)\/\d+\/pitfalls\/(\d+)$/);
      if (!item) return block.anchor;
      var reference = readerSupportReference(block);
      if (reference) return reference;
      var anchor = String(block.anchor || block.owner_id || 'support');
      return (/^reader-/.test(anchor) ? anchor : 'reader-' + anchor) + '-pitfall-' + item[1];
    }
    function readerSupportSummary(kind, title, anchor) {
      return '<summary><a class="reader-support-link" data-reader-link-kind="' + escapeHtml(kind) +
        '" href="#' + escapeHtml(anchor) + '"><span class="reader-link-kind reader-annotation-kind">' + escapeHtml(kind) + '</span>' +
        (title ? ' · <span class="reader-link-label">' + renderInline(title) + '</span>' : '') + '</a></summary>';
    }

    function readerConceptRegistrationTarget(blocks, document, layout) {
      // A name-only catalog registration is not another teaching paragraph.
      // Only consolidate an exact identity/name match with its real definition;
      // authored definitions, unresolved names and media hosts remain visible.
      if (!blocks.length || blocks.some(function (block) {
        return block.group !== 'concepts' || !/^\/concepts\/\d+\/name$/.test(block.json_pointer || '');
      })) return '';
      var first = blocks[0], name = String(first.markdown || first.raw_text || '').trim();
      if (blocks.some(function (block) { return String(block.markdown || block.raw_text || '').trim() !== name; })) return '';
      if (((layout || {}).mounts || []).some(function (mount) {
        return blocks.some(function (block) { return block.json_pointer === mount.target_pointer; });
      })) return '';
      var references = (document.blocks || []).filter(function (block) {
        return block.group === 'references' && block.owner_id === first.owner_id;
      });
      var label = references.find(function (block) {
        return /\/label$/.test(block.json_pointer || '') && String(block.markdown || block.raw_text || '').trim() === name;
      });
      return label && references.some(function (block) {
        return block.anchor === label.anchor && /\/markdown$/.test(block.json_pointer || '') && String(block.markdown || block.raw_text || '').trim();
      }) ? label.anchor : '';
    }

    // V4 consumes the same frozen blocks as review; there are no field aliases here.
    function readerDocumentHtml(document, region, media, layout, presentation) {
      readerPresentation = null;
      if (presentation) {
        var binding=[document.unit_id,document.content_hash].concat(document.blocks.map(function(b){return ['block_id','json_pointer','raw_text','markdown','visible_text','anchor','group','owner_id','field_label'].map(function(k){return String(b[k]===undefined?'':b[k]);}).concat([(b.source_map||[]).map(String),(b.marker_spans||[]).map(function(span){return ['visible_start','visible_end','raw_start','raw_end'].map(function(k){return String(span[k]===undefined?'':span[k]);});})]);}));
        if(presentation.schema_version!=='TeachingPresentationV1' || presentation.content_hash!==document.content_hash || !layout || presentation.reader_document_hash!==layout.reader_document_hash || presentation.binding_hash!==presentationHash(JSON.stringify(binding))) throw new Error('Stale teaching presentation');
        var known={};document.blocks.forEach(function(field){readerMarkdown.parseReaderField(field.markdown,{marked:marked,field:field}).blocks.forEach(function(b){known[b.block_id]=b;});});
        Object.keys(presentation.roles||{}).forEach(function(id){if(!known[id] || !['main','proof','worked_example'].includes(presentation.roles[id])) throw new Error('Invalid teaching presentation role');});
        var seenRegions={};(presentation.regions||[]).forEach(function(r){var b=known[r.block_id];if(seenRegions[r.block_id] || !b||b.field_id!==r.reader_block_id||b.target_pointer!==r.reader_pointer||b.source_start_cp!==r.source_start_cp||b.source_end_cp!==r.source_end_cp||presentation.roles[r.block_id]!==r.reader_role)throw new Error('Invalid teaching presentation region');seenRegions[r.block_id]=true;});
        if(Object.keys(presentation.roles||{}).length!==(presentation.regions||[]).length) throw new Error('Incomplete teaching presentation regions');
        readerPresentation=presentation;
      }
      readerMedia = media || [];
      readerLayout = layout || null;
      if (!document || document.schema_version !== "ReaderDocumentV1") {
        throw new Error("ReaderDocumentV1 required");
      }
      if (readerMedia.length && !readerLayout) throw new Error('ReaderLayoutV1 required for current reader media');
      if (readerLayout && readerLayout.schema_version !== 'ReaderLayoutV1') throw new Error('ReaderLayoutV1 required');
      var groups = [], byKey = {};
      document.blocks.forEach(function (block) {
        var header = block.group === "title" || block.group === "one_line";
        if ((region === "header" && !header) || (region === "body" && header)) return;
        var questionPosition = block.group === "learning_checks" && block.json_pointer.match(/^\/learning_checks\/\d+(?=\/|$)/);
        var key = questionPosition ? questionPosition[0] : block.group + "/" + block.owner_id;
        if (!byKey[key]) { byKey[key] = []; groups.push(byKey[key]); }
        byKey[key].push(block);
      });
      var out = ['<div class="reader-document">'];
      groups.forEach(function (blocks) {
        var first = blocks[0], collapsed = first.collapsed || first.group === "examples", tag = collapsed ? "details" : "section", groupAnchor = first.anchor;
        var registrationTarget = readerConceptRegistrationTarget(blocks, document, readerLayout);
        if (first.disclosure_kind === 'theorem') {
          out.push('<section class="reader-group reader-theorem-content" data-reader-group="explainables" data-reader-owner="' + escapeHtml(first.owner_id) + '" id="' + escapeHtml(groupAnchor) + '">');
          out.push('<div class="reader-theorem-core">');
          blocks.filter(function (block) { return block.disclosure_part === 'core'; }).forEach(function (block) { out.push(readerBlockHtml(block)); });
          out.push('</div>');
          var proofBlocks = blocks.filter(function (block) { return block.disclosure_part !== 'core'; });
          if (proofBlocks.length) out.push('<details class="reader-theorem-proof"><summary>展开完整证明</summary>' + proofBlocks.map(function (block) { return readerBlockHtml(block); }).join('') + '</details>');
          out.push('</section>'); return;
        }
        var supportKind = {explainables:'解释', references:'概念'}[first.group] || '';
        if (first.group === 'annotations') supportKind = ['说明','直觉','易错','补充推导'].indexOf(first.field_label) >= 0 ? first.field_label : '说明';
        var support = first.collapsed && supportKind;
        if (first.group === "learning_checks") {
          var checks = (document.learning_checks || []).filter(function (row) { return row.id === first.owner_id; });
          var questions = groups.filter(function (group) { return group[0].group === "learning_checks" && group[0].owner_id === first.owner_id; });
          if (checks.length === 1 && questions.length === 1) { out.push(learningCheckHtml(blocks,checks[0])); return; }
          if (questions.length > 1) groupAnchor += "-question-" + first.json_pointer.split("/")[2];
          // Keep the question, hints and answer readable when scoring metadata
          // is absent or ambiguous. Never borrow another question's grading.
        }
        out.push('<' + tag + ' class="reader-group' + (support ? ' reader-support-item' : '') + '" data-reader-group="' + escapeHtml(first.group) +
          '" data-reader-owner="' + escapeHtml(first.owner_id) + '" id="' + escapeHtml(groupAnchor) + '"' +
          (support ? ' data-reader-support-kind="' + escapeHtml(supportKind) + '"' : '') +
          (registrationTarget ? ' hidden data-reader-concept-registration="' + escapeHtml(registrationTarget) + '"' : '') + '>');
        if (collapsed) {
          if (first.group === "examples") {
            var exampleLabel = blocks.find(function (block) { return /\/label$/.test(block.json_pointer); });
            out.push('<summary>' + renderInline(exampleLabel ? exampleLabel.markdown || exampleLabel.raw_text : "例题") + '</summary>');
          } else if (support) {
            var titleField = blocks.find(function (block) { return /\/(title|name|label)$/.test(block.json_pointer); });
            out.push(readerSupportSummary(supportKind, titleField ? titleField.markdown || titleField.raw_text : '', groupAnchor));
          } else {
            out.push('<summary>' + escapeHtml(first.group === 'learning_checks' ? '随文自测' : first.field_label) + '</summary>');
          }
        }
        if (first.group === "learning_checks") {
          out.push('<p>本题暂不支持自动评分，请对照答案自查。</p>');
        }
        var reviewKey = "";
        blocks.forEach(function (block) {
          // Wrap only adjacent items from the same authored array. Blocks,
          // pointers and their media mounts retain their original order.
          var review = block.json_pointer.match(/^(\/(?:examples|explainables)\/\d+)\/(key_points|pitfalls)\/\d+$/);
          var nextKey = review ? review[1] + '/' + review[2] : "";
          if (nextKey !== reviewKey) {
            if (reviewKey) out.push('</div>');
            if (review) out.push('<div class="reader-teaching-review" data-reader-review="' + review[2] + '"><h3>' +
              (review[2] === 'key_points' ? '解后回顾' : '易错提醒') + '</h3>');
            reviewKey = nextKey;
          }
          if (review && review[2] === 'pitfalls') {
            var supportAnchor = readerSupportTarget(block);
            if (readerSupportReference(block)) {
              // A pure reference already leads to the full existing note.
              out.push(readerBlockHtml(block, ' data-reader-support-reference-only="true"'));
            } else {
              out.push('<details class="reader-support-item reader-pitfall-item" id="' + escapeHtml(supportAnchor) +
                '" data-reader-support-kind="易错">' + readerSupportSummary('易错', '第 ' + (Number(block.json_pointer.split('/').pop()) + 1) + ' 点', supportAnchor));
              out.push(readerBlockHtml(block)); out.push('</details>');
            }
          } else out.push(readerBlockHtml(block));
        });
        if (reviewKey) out.push('</div>');
        out.push('</' + tag + '>');
      });
      out.push('</div>');
      return out.join("");
    }

    function theoremReaderHtml(entry) {
      // The producer supplies the actual theorem fields and dependency closure;
      // the reader never guesses a theorem from a matching display name.
      readerMedia = []; readerLayout = null;
      var document = entry.reader_document, core = (entry.core_blocks || []).slice(), proof = (entry.proof_blocks || []).slice();
      var selected = new Set(core.concat(proof).map(function (block) { return block.block_id; }));
      (document.blocks || []).forEach(function (block) {
        if (block.anchor === entry.anchor && !selected.has(block.block_id)) { core.push(block); selected.add(block.block_id); }
      });
      var out = ['<div class="reader-document"><section class="reader-theorem-content" id="' + escapeHtml(entry.anchor) + '">',
        '<div class="reader-theorem-core">' + core.map(function (block) { return readerBlockHtml(block); }).join('') + '</div>'];
      if (entry.source_label) out.push('<p class="reader-theorem-source">' + escapeHtml(entry.source_label) + '</p>');
      if (proof.length) out.push('<details class="reader-theorem-proof"><summary>展开完整证明</summary>' +
        proof.map(function (block) { return readerBlockHtml(block); }).join('') + '</details>');
      out.push('</section>');
      var dependencies = Object.assign({}, document, {blocks:document.blocks.filter(function (block) { return !selected.has(block.block_id); })});
      if (dependencies.blocks.length) out.push(readerDocumentHtml(dependencies, 'body'));
      out.push('</div>');
      return out.join('');
    }

    return { renderMarkdown: renderMarkdown, renderOriginal: renderOriginal,
             renderInline: renderInline, renderSourceMaterial: renderSourceMaterial,
             /* 界面上要按编号找锚点（P-035 的跳转），这个 id 规则只有一处定义，
                必须从这里借出去 —— 界面那边**不许再写一遍**（写了就会像刚才那样对不上）。 */
             numAnchorId: numAnchorId,
             renderBody: renderBody, cardOf: cardOf,
             referencedConcepts: referencedConcepts,
             renderFigure: figureHtml, escapeHtml: escapeHtml,
             examplesHtml: examplesHtml, readerDocumentHtml: readerDocumentHtml, theoremReaderHtml: theoremReaderHtml,
             readerSupportTarget: readerSupportTarget, readerConceptRegistrationTarget: readerConceptRegistrationTarget,
             gradeLearningCheck: gradeLearningCheck,
             beginUnit: beginUnit,
             noteOrder: function () { return unitCtx ? unitCtx.noteOrder.slice() : []; },
             explainOrder: function () { return unitCtx ? unitCtx.explainOrder.slice() : []; } };
  }

  /** 目录树：只保留"有内容（在 scope 里）或是它祖先"的节点。 */
  function filterTree(nodes, scope) {
    var keep = {};
    scope.forEach(function (id) { keep[id] = true; });
    function walk(list) {
      var out = [];
      (list || []).forEach(function (node) {
        var children = walk(node.children || node.sections || node.subsections);
        var has = keep[node.id] || children.length > 0;
        if (!has) return;
        out.push({ id: node.id, anchor: node.anchor, title: node.title,
                   kind: node.kind, unit: !!keep[node.id], children: children });
      });
      return out;
    }
    return walk(nodes);
  }

  return { createRenderer: createRenderer, filterTree: filterTree, escapeHtml: escapeHtml,
           plainMath: plainMath,
           mask: mask, REF: REF, NUM_REF: NUM_REF, numAnchorId: numAnchorId,
           linkNumbers: linkNumbers, anchorNumbers: anchorNumbers };
});
