/* 用 jsdom 把「MathBox 冒烟测试页」的逻辑真的跑一遍 —— 浏览器我看不到，
 * 但"滑块有没有接上、分步有没有生效、回执文字对不对"这些**纯逻辑**能自动验。
 *
 * 做法：把 MathBox 换成一个假的（记录每一次图元调用与每一次 .set），
 * 于是页面的每一句"接线"都能被断言。这也正是以后做真模型层要用的检查方式。
 *
 * 用法：
 *   NODE_PATH=../../.devtools/node_modules node check_mathbox.js <页面路径>
 *   NODE_PATH=../../.devtools/node_modules node check_mathbox.js <页面路径> --no-mathbox
 *
 * 退出码：0 通过 / 1 有问题 / 3 没有 jsdom（跳过，不算失败）。
 */
const fs = require("fs");
const path = require("path");

const page = path.resolve(process.argv[2] || ".");
const noMathBox = process.argv.includes("--no-mathbox");

let JSDOM;
try {
  ({ JSDOM } = require("jsdom"));
} catch (error) {
  console.error("没有 jsdom，跳过。装法：npm install jsdom --prefix .devtools --cache /tmp/npmcache");
  process.exit(3);
}

const problems = [];
const notes = [];
function check(cond, msg) {
  if (!cond) problems.push(msg);
}
function note(msg) { notes.push(msg); }

const html = fs.readFileSync(page, "utf8");

/* 假图元结点：记录被调用了什么、被 set 了什么 */
function makeNode(name, rec) {
  const node = {
    _name: name,
    set(k, v) { rec.sets.push({ node: name, key: k, value: v }); },
    get() { return undefined; },
  };
  ["grid", "axis", "array", "vector", "face", "line", "point", "label", "html",
   "interval", "cartesian", "transform", "group", "slide", "present", "camera",
   "area", "matrix", "text", "format"].forEach(function (m) {
    node[m] = function () {
      const args = Array.prototype.slice.call(arguments);
      rec.calls.push({ node: name, method: m, args: args });
      return makeNode(name + "." + m, rec);
    };
  });
  return node;
}

function makeFakeThree() {
  /* 假向量要够用：页面会 clone() 再 distanceTo()（拖动自测用） */
  function V3(x, y, z) {
    this.x = x; this.y = y; this.z = z;
    this.clone = function () { return new V3(x, y, z); };
    this.distanceTo = function () { return 0; };
  }
  return {
    REVISION: "137",
    Color: function (c) { this.c = c; },
    Vector3: V3,
    OrbitControls: function () {},
    _gl: { calls: 12, triangles: 3456 },
  };
}

const instances = [];

(async function main() {
  const dom = await JSDOM.fromFile(page, {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(window) {
      const THREE = makeFakeThree();
      window.THREE = THREE;
      window.katex = {
        version: "0.16.9",
        renderToString: function (s) { return '<span class="k">' + s + "</span>"; },
      };
      if (!noMathBox) {
        window.MathBox = {
          mathBox: function (opts) {
            const rec = { opts: opts, calls: [], sets: [] };
            instances.push(rec);
            const root = makeNode("root", rec);
            root.three = {
              renderer: {
                setClearColor() {},
                info: { render: { calls: 12, triangles: 3456 } },
                /* 页面会在画布上派发拖动事件（自测），假的画布要能接住 */
                domElement: { dispatchEvent() {}, width: 1140, height: 620, getContext() { return null; } },
              },
              camera: { position: { set() {} }, lookAt() {} },
              controls: { target: { set() {} }, update() {} },
            };
            return root;
          },
        };
      }
      window.addEventListener("error", function (e) {
        problems.push("页面抛出异常：" + e.message + " @" + (e.lineno || "?"));
      });
    },
  });

  const { window } = dom;
  const doc = window.document;
  const $ = (s) => doc.querySelector(s);

  function fire(el, type) {
    el.dispatchEvent(new window.Event(type, { bubbles: true }));
  }

  if (noMathBox) {
    /* 场景二：MathBox 根本没加载 —— 页面必须还能"报丧" */
    ["a", "b", "c"].forEach(function (k) {
      const st = $("#stage-" + k);
      check(st && st.className.indexOf("fail") >= 0, "MathBox 缺失时 #stage-" + k + " 应显示失败提示");
      check(st && /MathBox/.test(st.textContent), "MathBox 缺失时 #stage-" + k + " 的提示文字里应出现 MathBox");
    });
    $("#btn-report").click();
    const rep = $("#report").value;
    check(rep.length > 80, "MathBox 缺失时「生成回执」也要能出内容（否则用户没法报丧）");
    check(/出错/.test(rep), "回执里应写明状态是出错");
    check(/MathBox/.test(rep), "回执里应出现 MathBox 字样");
    check(/\[是\]|\[否\]/.test(rep), "回执里应有清单");
    check(rep.indexOf("[log]") < 0, "回执里不该塞原始日志");
    report("无 MathBox 时的报丧路径");
  } else {
    /* 场景一：MathBox 在（假的），验接线 */
    check(instances.length === 3, "应起 3 个 MathBox 实例，实际 " + instances.length);
    /* 画布尺寸：宽度跟着容器（jsdom 没有布局，会落到下限 300），高度按横竖比跟宽度走 */
    const wantSize = [{ r: 0.58, cap: 660 }, { r: 0.54, cap: 620 }, { r: 0.42, cap: 480 }];
    instances.forEach(function (rec, i) {
      const size = rec.opts && rec.opts.size;
      check(!!size, "第 " + (i + 1) + " 个实例没传 size");
      if (size) {
        check(size.width >= 300 && size.width <= 1180, "第 " + (i + 1) + " 个实例画布宽度不合理：" + size.width);
        const want = Math.round(Math.min(wantSize[i].cap, Math.max(240, size.width * wantSize[i].r)));
        check(size.height === want, "第 " + (i + 1) + " 个实例高度应随宽度算成 " + want + "，实际 " + size.height);
      }
      check(rec.opts.controls && rec.opts.controls.klass, "第 " + (i + 1) + " 个实例没给 controls.klass");
      check(Array.isArray(rec.opts.plugins) && rec.opts.plugins.indexOf("core") >= 0, "第 " + (i + 1) + " 个实例没启用 core");
    });
    if (instances.length === 3) {
      const [A, B, C] = instances;
      /* ① 能转，②③ 锁住旋转（否则 2D 图会被拖歪 = “不可看”） */
      check(!A.opts.controls.parameters || A.opts.controls.parameters.enableRotate !== false,
        "① 应该允许旋转视角");
      check(B.opts.controls.parameters && B.opts.controls.parameters.enableRotate === false,
        "② 应锁定旋转（2D 图不能被拖歪）");
      check(C.opts.controls.parameters && C.opts.controls.parameters.enableRotate === false,
        "③ 应锁定旋转");

      const methods = (rec) => rec.calls.map((c) => c.method);
      const count = (rec, m) => methods(rec).filter((x) => x === m).length;
      check(count(A, "axis") === 3, "① 应有 3 根坐标轴，实际 " + count(A, "axis"));
      check(count(A, "grid") === 1, "① 应有 1 个网格");
      check(count(A, "vector") === 5, "① 应有 5 根箭头（1 固定 + 1 受矩阵控制 + 3 分步），实际 " + count(A, "vector"));
      check(count(A, "face") === 1, "① 应有 1 个受矩阵控制的方块");
      check(count(A, "transform") === 1, "① 应恰好有 1 个 transform");
      check(count(A, "slide") === 3, "① 应有 3 步");
      check(count(B, "axis") === 2, "② 应是 2 根轴（平面图）");
      /* 两点 line 在这版 MathBox 里画不出来，对角线必须用 vector（顺便锁住这个坑） */
      check(count(B, "vector") >= 1, "② 的对角线应该用 vector 画（两点 line 画不出来）");
      check(count(B, "html") === 0, "② 不应再有场景内 html 图元（实测它不工作，公式改放画布外）");
      check(count(C, "interval") === 1, "③ 应用 interval 画曲线");

      /* range 必须正方：横竖比不等会让图形变形（实测 0.54），这条锁住别再犯 */
      instances.forEach(function (rec, i) {
        const car = rec.calls.filter((c) => c.method === "cartesian")[0];
        check(!!car, "实例 " + (i + 1) + " 没建 cartesian");
        if (car) {
          const r = car.args[0].range;
          const ex = r[0][1] - r[0][0], ey = r[1][1] - r[1][0];
          check(Math.abs(ex - ey) < 1e-6, "实例 " + (i + 1) + " 的 range 不是正方（" + ex + " vs " + ey + "）→ 图形会变形");
        }
      });

      /* 初始矩阵：单位矩阵，列主序 */
      const initMat = A.sets.filter((s) => s.key === "matrix")[0];
      check(!!initMat, "① 的 transform 初始没设 matrix");
      if (initMat) {
        const want = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
        check(JSON.stringify(initMat.value) === JSON.stringify(want),
          "初始矩阵应是单位阵，实际 " + JSON.stringify(initMat.value));
      }

      /* 滑块 → 矩阵（列主序：[[a,b],[c,d]] → [a,c,...,b,d,...]） */
      $("#sa").value = "2"; fire($("#sa"), "input");
      let last = A.sets.filter((s) => s.key === "matrix").pop();
      check(last.value[0] === 2 && last.value[5] === 1, "a=2 应落在矩阵第 0 位（列主序），实际 " + JSON.stringify(last.value.slice(0, 6)));
      $("#sb").value = "0.5"; fire($("#sb"), "input");
      last = A.sets.filter((s) => s.key === "matrix").pop();
      check(last.value[4] === 0.5, "b=0.5 应落在矩阵第 4 位（列主序），实际 " + JSON.stringify(last.value.slice(0, 6)));
      $("#sc").value = "-1.5"; fire($("#sc"), "input");
      last = A.sets.filter((s) => s.key === "matrix").pop();
      check(last.value[1] === -1.5, "c=-1.5 应落在矩阵第 1 位（列主序），实际 " + JSON.stringify(last.value.slice(0, 6)));

      /* 分步：点两下到第 3 步，再点不会越界 */
      $("#btn-next").click();
      $("#btn-next").click();
      check($("#step-now").textContent === "3", "点两下后应显示第 3 步，实际 " + $("#step-now").textContent);
      const idx = A.sets.filter((s) => s.key === "index").pop();
      check(idx && idx.value === 3, "present 的 index 应被设成 3");
      $("#btn-next").click();
      check($("#step-now").textContent === "3", "第 3 步后不该再往上加");
      $("#btn-reset").click();
      check($("#step-now").textContent === "1", "「回到第 1 步」应把 index 复位成 1");

      /* 图下面的公式：必须是普通 HTML（不是画布里的贴片） */
      const kf = $("#katex-fig");
      check(!!kf, "② 卡片里应有 #katex-fig（图下面的公式）");
      check(kf && kf.innerHTML.length > 10, "图下面的公式应该真的渲染出内容");
      check(kf && kf.querySelector("span.k"), "公式应该由 KaTeX 渲染（有 span.k 结构）");

      /* 三张图的状态行：必须写出“画了多少” */
      await new Promise((r) => setTimeout(r, 6000));   /* 页面里 1.8s 拖动自测 / 2.5s 测绘制数，留足时间 */
      ["a", "b", "c"].forEach(function (k) {
        const txt = $("#status-" + k).textContent;
        check(/绘制/.test(txt), "#status-" + k + " 应写出实际绘制数量，实际：" + txt);
        check(/已构建/.test(txt), "#status-" + k + " 应显示已构建，实际：" + txt);
        /* 页面自己派发拖动、量相机位移（1.8 秒后才会有值） */
        check(/拖动自测/.test(txt), "#status-" + k + " 应报告拖动自测结果，实际：" + txt.slice(0, 90));
      });

      /* 回执 */
      $("#c1").checked = true;
      $("#btn-report").click();
      const rep = $("#report").value;
      check(/① 三维主场景: 已构建/.test(rep), "回执应含第 ① 张图的状态行");
      check(/绘制调用=12/.test(rep), "回执应含绘制调用数");
      check(/\[是\]/.test(rep) && /\[否\]/.test(rep), "回执应含清单的是/否");
      check(/屏幕:/.test(rep) && /WebGL/.test(rep), "回执应含屏幕与 WebGL 信息");
      check(rep.indexOf("[log]") < 0, "回执里不该塞原始日志（那才是看着像乱码的东西）");
      check(!/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(rep), "回执里不该有控制字符");
      check(rep.length < 4000, "回执应短小，实际 " + rep.length + " 字");

      /* 原始日志留了单独入口 */
      $("#btn-raw").click();
      check($("#rawlog").value.length > 0, "「生成原始日志」应能出内容");

      report("有 MathBox（假）时的接线");
    }
  }

  window.close();

  function report(what) {
    if (problems.length === 0) {
      console.log("✅ " + what + "：全部通过（" + notes.length + " 条备注）");
    } else {
      console.log("❌ " + what + "：" + problems.length + " 个问题");
      problems.forEach((p) => console.log("   - " + p));
    }
  }
  process.exit(problems.length === 0 ? 0 : 1);
})().catch(function (e) {
  console.error("检查脚本自己出错：" + (e && e.stack ? e.stack : e));
  process.exit(1);
});
