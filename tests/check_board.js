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
  ok(text("past").includes("残した"), "past: shows what was left");
  ok(d.querySelectorAll("#next .ask .btn").length === 3, "future: the open question has 3 answer buttons");

  // choose an older run: past / present / future follow it
  nodes()[3].click(); await tick();
  ok(pressed()?.getAttribute("data-id") === "4", "choose: run 4 is chosen");
  ok(text("past").includes("天気 API をつなぐ途中で止まった"), "choose: past shows run 4");
  ok(text("now").includes("第 2 段: 天気をつなぐ") && text("now").includes("時点の状態"), "choose: present shows run 4's section");
  ok(text("next").includes("手元のサーバで開き直す"), "choose: future shows run 4's next");
  ok(text("next").includes("実際にこのあと") && text("next").includes("天気 API から 7 日分を出した"), "choose: future shows what really came next");
  ok(!!d.querySelector(".viewing"), "choose: banner says a past point is shown");

  // git diff opens in the wide dialog; a small diff shows every file at once
  btn(d.getElementById("past"), "差分を見る").click(); await tick();
  const dd = d.getElementById("dlg");
  ok(dd.open && dd.querySelector("#dlg-h").textContent.includes("差分"), "diff: opens in the dialog");
  const f = dd.querySelector(".diff-file");
  ok(f && f.textContent.includes("api.js"), "diff: lists api.js");
  ok(f.open && f.querySelector("pre.diff .a")?.textContent.includes("export async function forecast"), "diff: a small diff is open with added lines marked");
  ok(dd.querySelector(".dlg-h .t").textContent.includes("天気 API をつなぐ途中で止まった"), "diff: the dialog names the run");
  btn(dd, "前の回").click(); await tick(); await tick();
  ok(pressed()?.getAttribute("data-id") === "3" && dd.querySelector(".diff-file code").textContent === "app.js", "diff: previous run moves the dialog and the board together");
  btn(dd, "次の回").click(); await tick(); await tick();
  btn(dd, "閉じる").click();
  ok(!dd.open && pressed()?.getAttribute("data-id") === "4", "diff: closes back on run 4");
  clean("past run with diff open");

  // the conversation opens in a wide dialog; tool calls fold into one line, edits show only the path
  const dlg = d.getElementById("dlg");
  ok(!dlg.open, "talk: the dialog starts closed");
  btn(d.getElementById("past"), "会話を読む").click(); await tick();
  ok(dlg.open, "talk: the button opens the dialog");
  ok(dlg.querySelector(".dlg-h").textContent.includes("天気 API をつなぐ途中で止まった"), "talk: the dialog names the run");
  ok(dlg.querySelector(".msg.user")?.textContent.includes("本物の天気を出したい"), "talk: shows what the user asked in run 4");
  const ans = [...dlg.querySelectorAll(".msg.assistant .md")].pop();
  ok(ans?.querySelector("strong")?.textContent === "天気 API をつなぐ途中で止まった", "markdown: bold");
  ok(ans?.querySelector("table td")?.textContent === "東京の 7 日分", "markdown: table");
  ok(ans?.querySelector("ul li")?.textContent.includes("画面への反映"), "markdown: list");
  ok(ans?.querySelector("code")?.textContent === "api.js", "markdown: inline code");
  ok(!ans?.textContent.includes("**") && !ans?.textContent.includes("|---"), "markdown: no raw marks left");
  const ops = dlg.querySelector("details.ops");
  ok(ops && !ops.open && /操作 \d+ ・ ✎ 編集 1/.test(ops.querySelector("summary").textContent), `talk: tool calls folded (${ops?.querySelector("summary").textContent})`);
  ok([...ops.querySelectorAll(".op.edit")].map(x => x.textContent).join() === "✎ api.js → ±", "talk: an edit shows only its path, with a way to its diff");
  ok(!/\[object|\bfalse\b|\bundefined\b|\bnull\b/.test(dlg.textContent), "talk: no stray false / undefined / [object] text");
  const tabs = [...dlg.querySelectorAll(".tabs .tab")];
  ok(tabs.map(x => x.textContent).join() === "💬 会話,± 差分" && tabs[0].getAttribute("aria-selected") === "true", "tabs: talk and diff, talk selected");
  ops.open = true;
  ops.querySelector("button.op.edit").click(); await tick(); await tick();
  const hitFile = dlg.querySelector(".diff-file.hit");
  ok(hitFile?.open && hitFile.querySelector("code").textContent === "api.js", "tabs: an edit line opens the diff at that file");
  ok(dlg.querySelector('.tab[aria-selected="true"]')?.textContent.includes("差分"), "tabs: the diff tab is selected");
  [...dlg.querySelectorAll(".tab")].find(x => x.textContent.includes("会話")).click(); await tick(); await tick();
  ok(!!dlg.querySelector(".msg.user"), "tabs: back to the talk");
  btn(dlg, "次の回").click(); await tick(); await tick();
  ok(dlg.textContent.includes("手元のサーバで開いて") && pressed()?.getAttribute("data-id") === "5", "talk: next run moves the dialog and the board together");
  btn(dlg, "閉じる").click();
  ok(!dlg.open, "talk: closes");

  // cross filter: a section dims runs outside it
  btn(d.getElementById("flow"), "第 1 段").click(); await tick();
  ok(nodes().filter(n => !n.classList.contains("dim")).length === 3, "filter: section 1 leaves 3 runs lit");
  ok(text("flow").includes("当てはまる 3 件"), "filter: count shown");
  btn(d.getElementById("flow"), "セクション: 第 1 段").click(); await tick();
  ok(nodes().every(n => !n.classList.contains("dim")), "filter: cleared");

  // cross filter: a task lights the runs that moved it
  d.querySelector('.node[data-id="7"]').click(); await tick();
  btn(d.getElementById("now"), "カンバン").click(); await tick();
  const kd = d.getElementById("dlg");
  ok(kd.open && kd.querySelectorAll(".kanboard .kcol").length === 4, "kanban: opens in the dialog with 4 columns");
  ok(!kd.querySelector(".tabs"), "kanban: no talk / diff tabs");
  const card = [...kd.querySelectorAll(".kcard")].find(c => c.textContent.includes("天気 API から取る"));
  btn(card, "履歴で見る").click(); await tick();
  ok(!kd.open, "kanban: choosing a task closes the dialog to show the history");
  const lit = nodes().filter(n => !n.classList.contains("dim")).map(n => n.getAttribute("data-id"));
  ok(JSON.stringify(lit) === JSON.stringify(["3", "4", "5"]), `filter: task t4 lights runs 3, 4, 5 (got ${lit})`);
  ok(d.querySelector("#flow .btn.filter[aria-pressed=true]")?.textContent.includes("タスク:"), "filter: shown as a pressed pill");
  d.querySelector('.node[data-id="2"]').click(); await tick();
  btn(d.getElementById("now"), "カンバン").click(); await tick();
  ok([...kd.querySelectorAll(".kcol")].map(c => c.querySelectorAll(".kcard").length).join() === "1,0,4,2", `kanban: shows run 2's columns (got ${[...kd.querySelectorAll(".kcol")].map(c => c.querySelectorAll(".kcard").length)})`);
  btn(kd, "閉じる").click();
  clean("task filter");

  // back to the newest and answer
  d.querySelector('.node[data-id="7"]').click(); await tick();
  btn(d.getElementById("flow"), "タスク:").click(); await tick();
  ok(nodes().every(n => !n.classList.contains("dim")), "filter: task cleared");
  const asks = d.querySelectorAll("#next .ask");
  asks[0].querySelectorAll(".btn")[1].click();
  btn(d.getElementById("next"), "答えをコピー").click(); await tick();
  ok(d.getElementById("copy-fallback").value.includes("→ Cloudflare Pages"), "copy: the chosen answer is in the text");
  // where each not_done item went: a task, dropped, or left behind
  d.querySelector('.node[data-id="1"]').click(); await tick();
  ok(d.querySelector("#past .tag.d-left")?.textContent.includes("置き去り"), "left: run 1's 見た目の調整 is left behind");
  ok(text("now").includes("この範囲の置き去り"), "left: present shows the section's left-behind count");
  const lg = d.querySelector('#now details[data-key="ledger"]');
  lg.open = true; await tick();
  ok(lg.textContent.includes("見た目の調整") && !!btn(lg, "その回へ"), "left: the section record lists it with a way to its run");
  clean("left behind");
  d.querySelector('.node[data-id="2"]').click(); await tick();
  ok(d.querySelector("#past .tag.d-task")?.textContent.includes("天気 API から取る"), "left: run 2's 本物の天気 went to task t4");
  d.querySelector('.node[data-id="7"]').click(); await tick();
  const nd7 = d.querySelector('#past details[data-key="notdone"]');
  ok(nd7.textContent.includes("やらないと決めた（1）") && nd7.textContent.includes("続きはタスクへ（1）"), "left: run 7 splits 公開 (to a task) from アイコン (dropped)");
  ok(!nd7.textContent.includes("しなかった"), "left: nothing reads as 'not done' when it went to a task");
  ok(!text("now").includes("この範囲の置き去り"), "left: section 3 has nothing left behind");
  const lg3 = d.querySelector('#now details[data-key="ledger"]');
  ok(lg3.textContent.includes("続きはタスクへ（1）") && lg3.textContent.includes("やらないと決めた（1）"), "left: section 3's record has 1 to a task and 1 dropped");
  lg3.open = false;

  // evaluation fixes (2026-10-20): what a reader without context got stuck on
  const legend = d.querySelector(".flow-legend");
  ok(legend && /失敗\s*1/.test(legend.textContent) && /全\s*7\s*回/.test(legend.textContent), "F: the history legend counts each kind (失敗 1, 全 7 回)");
  ok(text("now").includes("段取り "), "H: the section progress says it counts 段取り");
  const didFold = d.querySelector('#past details[data-key="did"]');
  ok(didFold.textContent.includes("git から"), "D: changed files come from git when there is git");
  // G: find a run by its title
  btn(d.getElementById("flow"), "回の一覧").click(); await tick();
  const rd = d.getElementById("dlg");
  ok(rd.open && rd.querySelectorAll(".runrow").length === 7, "G: the run list shows all 7 runs");
  const q = rd.querySelector(".runq"); q.value = "API"; q.dispatchEvent(new w.Event("input")); await tick();
  ok(rd.querySelectorAll(".runrow").length === 2, `G: filtering by "API" leaves 2 runs (got ${rd.querySelectorAll(".runrow").length})`);
  rd.querySelector(".runrow").click(); await tick();
  ok(!rd.open && text("past").includes("天気 API から 7 日分を出した"), "G: choosing a row shows that run and closes the list");
  clean("run list");
  // E: the whole project's left-over list
  d.querySelector('.node[data-id="7"]').click(); await tick();
  btn(d.getElementById("now"), "残したこと").click(); await tick();
  ok(rd.open && rd.textContent.includes("見た目の調整") && rd.textContent.includes("アイコン") && rd.textContent.includes("第 1 段"),
    "E: the left-over list spans every section, with the section of each item");
  ok(rd.textContent.includes("置き去り（行き先未定）（1）"), "E: one item is left behind in the whole project");
  btn(rd, "閉じる").click();
  // I: a task card lists what was routed to it
  btn(d.getElementById("now"), "カンバン").click(); await tick();
  const t4 = [...rd.querySelectorAll(".kcard")].find(c => c.textContent.includes("天気 API から取る"));
  ok(t4 && t4.textContent.includes("回された残したこと（2）"), "I: the task card of t4 lists the 2 items routed to it");
  btn(rd, "閉じる").click();
  clean("left list and kanban");

  // t22: a user line made by 答えをコピー says so
  ok(w.fromBoard("どこに公開しますか？ → GitHub Pages") && w.fromBoard("A？ → x\nB? → y"), "t22: a copied answer is recognised");
  ok(!w.fromBoard("天気のウィジェットを作りたい") && !w.fromBoard("どうする？"), "t22: an ordinary message is not");
  // t22: while a new run is in progress, the question of the run before stays in view
  const busy = JSON.parse(d.getElementById("sample").textContent);
  busy.status.run = { status: "running", title: "作業中の回", asks: [] };
  w.__busy = busy;
  await w.eval("src = bundleSource(window.__busy); loadAll(false)"); await tick();
  ok(text("next").includes("実行中の回の前の回") && text("next").includes("どこに公開しますか？") && text("next").includes("GitHub Pages（おすすめ）"),
    "t22: the previous run's question is shown while a run is in progress");
  clean("in-progress run");
  await w.eval("src = bundleSource(JSON.parse(document.getElementById('sample').textContent)); loadAll(false)"); await tick();
  ok(!text("next").includes("実行中の回の前の回"), "t22: back to the sample, the note is gone");

  // t14: ← → move between runs, and the chosen run lives in the URL
  const key = (k, target = d.body) => target.dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true }));
  d.querySelector('.node[data-id="7"]').click(); await tick();
  ok(w.location.hash === "", "t14: no hash at the newest run");
  key("ArrowLeft"); await tick();
  ok(pressed()?.getAttribute("data-id") === "6", "t14: ← moves to the run before");
  ok(w.location.hash === "#run=6", `t14: the URL keeps the chosen run (got ${w.location.hash})`);
  key("ArrowRight"); await tick();
  ok(pressed()?.getAttribute("data-id") === "7" && w.location.hash === "", "t14: → moves back to the newest and clears the URL");
  key("ArrowRight"); await tick();
  ok(pressed()?.getAttribute("data-id") === "7", "t14: → at the newest stays");
  d.querySelector('.node[data-id="4"]').click(); await tick();
  btn(d.getElementById("past"), "会話を読む").click(); await tick();
  key("ArrowRight"); await tick(); await tick();
  ok(dlg.open && pressed()?.getAttribute("data-id") === "5" && dlg.textContent.includes("手元のサーバで開いて"), "t14: → inside the dialog moves the dialog's run");
  // t14: search inside the conversation
  const tq = dlg.querySelector(".talk-q input");
  key("ArrowLeft", tq); await tick();
  ok(pressed()?.getAttribute("data-id") === "5", "t14: arrows typed in the search box do not move the run");
  const before = dlg.querySelectorAll(".talk > *").length;
  tq.value = "サーバ"; tq.dispatchEvent(new w.Event("input")); await tick();
  const marks = dlg.querySelectorAll(".talk mark.hl");
  ok(marks.length > 0 && [...marks].every(m => m.textContent === "サーバ"), `t14: search marks every hit (${marks.length})`);
  ok(dlg.querySelectorAll(".talk > *").length < before && /件の発言に \d+ か所/.test(dlg.querySelector(".talk-q").textContent), "t14: search leaves only the matching messages and counts them");
  tq.value = "見つからない語"; tq.dispatchEvent(new w.Event("input")); await tick();
  ok(dlg.querySelectorAll(".talk > *").length === 0 && dlg.querySelector(".talk-q").textContent.includes("見つかりません"), "t14: no hit says so");
  tq.value = ""; tq.dispatchEvent(new w.Event("input")); await tick();
  ok(dlg.querySelectorAll(".talk > *").length === before && !dlg.querySelector("mark.hl"), "t14: clearing the search shows everything again");
  btn(dlg, "閉じる").click();
  // t14: the chosen run as Markdown
  d.querySelector('.node[data-id="4"]').click(); await tick();
  btn(d.getElementById("past"), "Markdown でコピー").click(); await tick();
  const mdText = d.querySelector("#past .copy-fallback").value;
  ok(mdText.startsWith("## ") && mdText.includes("天気 API をつなぐ途中で止まった") && mdText.includes("### 次にやること") && mdText.includes("手元のサーバで開き直す"),
    "t14: Markdown copy has the title and the next step");
  ok(mdText.includes("`api.js`") && !/\[object|undefined|null/.test(mdText), "t14: Markdown copy lists the changed files, with no stray text");
  // t14: a link with #run= opens that run
  w.location.hash = "#run=3"; await tick(); await tick();
  ok(pressed()?.getAttribute("data-id") === "3", "t14: a #run= link chooses that run");
  d.querySelector('.node[data-id="7"]').click(); await tick();
  clean("t14");

  // t15: the map of sections, one row each
  const map = d.querySelector('#flow details[data-key="map"]');
  ok(map && !map.open && map.querySelector("summary").textContent.includes("3 個") && !map.querySelector("summary").textContent.includes("戻った"),
    "t15: the section map is folded, 3 sections, no return");
  ok(map.querySelectorAll(".mnode").length === 7 && map.querySelectorAll(".smap-area path").length === 6, "t15: one dot per run, one line between each");
  ok(map.querySelectorAll(".smap-labels span").length === 3, "t15: one row per section");
  map.querySelectorAll(".mnode")[1].click(); await tick();
  ok(pressed()?.getAttribute("data-id") === "2", "t15: a dot on the map chooses that run");
  // a later run that goes back to section 1 joins its row again
  const back = JSON.parse(d.getElementById("sample").textContent);
  const r7 = back.overview.find(r => String(r.id) === "7");
  r7.section_id = back.overview.find(r => String(r.id) === "1").section_id;
  back.status.section = { id: r7.section_id, title: r7.section_id, steps: [] };
  w.__back = back;
  await w.eval("src = bundleSource(window.__back); loadAll(false)"); await tick();
  const map2 = d.querySelector('#flow details[data-key="map"]');
  ok(map2.querySelector("summary").textContent.includes("戻った 1 回") && map2.querySelectorAll("path.back").length === 1, "t15: going back to section 1 is drawn as one return");
  ok(map2.querySelectorAll(".smap-labels span").length === 2, "t15: the return uses section 1's row, not a new one (section 3 had only run 7)");
  clean("section map");
  await w.eval("src = bundleSource(JSON.parse(document.getElementById('sample').textContent)); loadAll(false)"); await tick();

  // t24: a notice from the tool is not shown as the user's words
  const nt = w.talkItem({ role: "notice", text: 'Background command "Run the tests" completed', at: "2026-10-21T01:00:00Z" });
  ok(nt.classList.contains("notice") && !nt.classList.contains("user") && nt.textContent.includes("あなたの発言ではない") && nt.textContent.includes("Run the tests"),
    "t24: a notice is its own line, saying it is not the user");

  // markdown edge cases, rendered with the page's own md()
  const mdHtml = t => { const div = d.createElement("div"); div.append(...w.md(t)); return div; };
  let m = mdHtml("## 題\n\n- a\n  - b\n- c\n\n1. x\n2. y\n\n```js\nconst a = '<b>';\n```\n\n[link](javascript:alert(1)) [ok](https://example.com) <img src=x onerror=alert(1)>");
  ok(m.querySelector("h2")?.textContent === "題", "markdown: heading");
  ok(m.querySelector("ul ul li")?.textContent === "b", "markdown: nested list");
  ok(m.querySelectorAll("ol li").length === 2, "markdown: ordered list");
  ok(m.querySelector("pre code")?.textContent === "const a = '<b>';", "markdown: fenced code kept as text");
  ok(!m.querySelector("a[href^=javascript]") && m.querySelector("a[href='https://example.com']"), "markdown: only http(s) links");
  ok(!m.querySelector("img") && m.textContent.includes("<img"), "markdown: raw HTML stays text");
  m = mdHtml("1 行目\n2 行目");
  ok(m.querySelectorAll("p").length === 1 && m.querySelectorAll("br").length === 1, "markdown: a line break inside a paragraph");
  process.exitCode = failed ? 1 : 0;
})();
