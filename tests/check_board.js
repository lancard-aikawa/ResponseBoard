// Loads board/responseboard.html in jsdom with its embedded sample and checks the three cards,
// the history axis and the cross filters.
// Run: npm install && npm test
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "board", "responseboard.html"), "utf8");
const dom = new JSDOM("<!doctype html><html><head></head><body>" + src + "</body></html>", {
  runScripts: "dangerously", pretendToBeVisual: true, url: "https://example.invalid/",
  beforeParse(w) { w.matchMedia = () => ({ matches: false, addEventListener() {} }); },
});
const w = dom.window, d = w.document;
let failed = 0;
const ok = (cond, msg) => { console.log((cond ? "ok   " : "FAIL ") + msg); if (!cond) failed++; };
const tick = () => new Promise(r => setTimeout(r, 20));
const text = id => d.getElementById(id).textContent;
// no stray "false" / "undefined" / "[object ...]" text anywhere on the page
const clean = where => { const c = d.querySelector(".wrap").cloneNode(true); c.querySelectorAll("pre.diff").forEach(p => p.remove()); const t = c.textContent; ok(!/\[object|\bfalse\b|\bundefined\b|\bnull\b/.test(t), `${where}: no stray false / undefined / [object] text`); };
const nodes = () => [...d.querySelectorAll(".node")];
const pressed = () => d.querySelector('.node[aria-pressed="true"]');
const btn = (scope, label) => [...scope.querySelectorAll("button")].find(b => b.textContent.includes(label));

(async () => {
  await tick();
  // the embedded sample is what scripts/make_sample.py wrote
  const embedded = JSON.parse(d.getElementById("sample").textContent);
  const file = JSON.parse(fs.readFileSync(path.join(root, "samples", "board.sample.json"), "utf8"));
  ok(JSON.stringify(embedded) === JSON.stringify(file), "sample: embedded data matches samples/board.sample.json");

  for (const id of ["signal", "flow", "past", "now", "next"]) {
    ok(!text(id).includes("[object"), `${id}: no "[object ...]" text`);
    ok(text(id).trim().length > 0, `${id}: not empty`);
  }
  clean("start");
  // history axis: one lane per section, the newest run chosen
  ok(d.querySelectorAll(".lane").length === 3, "flow: 3 section lanes");
  ok(nodes().length === 7, "flow: 7 runs");
  ok(pressed() === nodes()[6], "flow: the newest run is chosen");
  ok(!d.querySelector(".viewing"), "flow: no 'viewing the past' banner at the newest");

  // details stay folded
  ok([...d.querySelectorAll("details.fold")].every(x => !x.open), "cards: every detail starts folded");
  ok(text("past").includes("しなかった"), "past: shows what was not done");
  ok(d.querySelectorAll("#next .ask .btn").length === 3, "future: the open question has 3 answer buttons");

  // choose an older run: past / present / future follow it
  nodes()[3].click(); await tick();
  ok(pressed()?.getAttribute("data-id") === "4", "choose: run 4 is chosen");
  ok(text("past").includes("天気 API をつなぐ途中で止まった"), "choose: past shows run 4");
  ok(text("now").includes("第 2 段: 天気をつなぐ") && text("now").includes("時点の状態"), "choose: present shows run 4's section");
  ok(text("next").includes("手元のサーバで開き直す"), "choose: future shows run 4's next");
  ok(text("next").includes("実際にこのあと") && text("next").includes("天気 API から 7 日分を出した"), "choose: future shows what really came next");
  ok(!!d.querySelector(".viewing"), "choose: banner says a past point is shown");

  // git diff opens lazily, file by file
  const diff = [...d.querySelectorAll("#past details.fold")].find(x => x.querySelector("summary").textContent.includes("差分"));
  ok(!!diff, "diff: a folded diff part for a git run");
  diff.open = true; diff.dispatchEvent(new w.Event("toggle")); await tick();
  const f = diff.querySelector(".diff-file");
  ok(f && f.textContent.includes("api.js"), "diff: lists api.js");
  f.open = true; f.dispatchEvent(new w.Event("toggle")); await tick();
  ok(f.querySelector("pre.diff .a")?.textContent.includes("export async function forecast"), "diff: added lines are marked");
  clean("past run with diff open");

  // cross filter: a section dims runs outside it
  btn(d.getElementById("flow"), "第 1 段").click(); await tick();
  ok(nodes().filter(n => !n.classList.contains("dim")).length === 3, "filter: section 1 leaves 3 runs lit");
  ok(text("flow").includes("当てはまる 3 件"), "filter: count shown");
  btn(d.getElementById("flow"), "セクション: 第 1 段").click(); await tick();
  ok(nodes().every(n => !n.classList.contains("dim")), "filter: cleared");

  // cross filter: a task lights the runs that moved it
  d.querySelector('.node[data-id="7"]').click(); await tick();
  const kan = [...d.querySelectorAll("#now details.fold")].find(x => x.querySelector("summary").textContent.includes("カンバン"));
  kan.open = true; kan.dispatchEvent(new w.Event("toggle"));
  const card = [...d.querySelectorAll("#now .kcard")].find(c => c.textContent.includes("天気 API から取る"));
  btn(card, "履歴で見る").click(); await tick();
  const lit = nodes().filter(n => !n.classList.contains("dim")).map(n => n.getAttribute("data-id"));
  ok(JSON.stringify(lit) === JSON.stringify(["3", "4", "5"]), `filter: task t4 lights runs 3, 4, 5 (got ${lit})`);
  ok(d.querySelector("#now details.fold[data-key=kanban]")?.open, "filter: the open kanban stays open after redraw");
  clean("task filter");

  // back to the newest and answer
  btn(d.getElementById("flow"), "タスク:").click(); await tick();
  ok(nodes().every(n => !n.classList.contains("dim")), "filter: task cleared");
  const asks = d.querySelectorAll("#next .ask");
  asks[0].querySelectorAll(".btn")[1].click();
  btn(d.getElementById("next"), "答えをコピー").click(); await tick();
  ok(d.getElementById("copy-fallback").value.includes("→ Cloudflare Pages"), "copy: the chosen answer is in the text");
  process.exitCode = failed ? 1 : 0;
})();
