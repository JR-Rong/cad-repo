/* Client half of @local/dsh-plugin-cad-freecad.
 *
 * Hand-written in the module-loader wire format the Host serves over /plugins
 * (identical shape to the shipped bundles: a `window.__ModuleLoader__.load`
 * call whose factory returns `{ apply, inject }`). Written by hand because the
 * desktop Host serves built bundles and this plugin ships no build step: the
 * file below IS the build output, and `dsh.client` in package.json points at
 * it.
 *
 * Only baseline modules may be require()d (react, react-dom, react/jsx-runtime,
 * @deepseek-ai/{cordis,dsh-client-store,dsh-client-ui-slots,
 * dsh-client-ui-primitives,dsh-client-ui-dockkit,zod,@tanstack/react-virtual}).
 * Everything else arrives through `dsh.client.inject` as a service on `ctx`,
 * which is also why every service use below is feature-detected: this panel
 * must render something honest even when an optional service is absent.
 */
window.__ModuleLoader__.load({
	id: "@local/dsh-plugin-cad-freecad",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		var react = require("react");

		//#region constants
		/** Dictionary namespace owned by this plugin. */
		var NS = "cadFreecad";
		/** The id shared by the sidebar entry and the main panel it opens. */
		var PANEL_ID = "cad-freecad";
		/** Snapshot written by the engine after every action (see entry.py). */
		var BOARD_PATH = "cad/mem/board.json";
		/** Reload the snapshot when it is older than this. */
		var POLL_MS = 5000;
		/** Action buttons: what the agent is asked to run, verbatim. */
		var ACTIONS = [
			{ id: "memcheck", prompt: "cad_freecad memcheck：报当前内存预算与下一个 export 的工作点。" },
			{ id: "validate", prompt: "cad_freecad validate（当前 CAD 项目）：跑验收门并把 FAIL 项列出来。" },
			{ id: "probe", prompt: "cad_freecad probe（当前 CAD 项目）：按内存预算自动定并发，逐个参数验证可编辑性。" },
			{ id: "export", prompt: "cad_freecad export（当前 CAD 项目）：STEP AP214 + 8 张交付图，超限自动降级并在 JSON 里记 degraded。" },
			{ id: "package", prompt: "cad_freecad package（当前 CAD 项目，batch 见看板）：逐项硬校验交付清单后出件。" },
		];
		var DICT = {
			zh: {
				"panel": "CAD 看板",
				"scope": "读取自会话",
				"title": "FreeCAD CAD 流水线",
				"subtitle": "参数化建模 · 验收门 · 内存治理",
				"refresh": "刷新",
				"loading": "读取 cad/mem/board.json …",
				"unavailable": "看板暂时读不到 cad/mem/board.json：面板通过当前会话的工作区读文件，请先打开一个会话（引擎每次动作都会重写这个快照，也可让 Agent 跑一次 cad_freecad board）。",
				"governor": "内存治理",
				"cap": "单进程上限",
				"floor": "降级下限",
				"workers": "probe 并发上限",
				"plan": "下次 export 工作点",
				"last": "最近一次任务",
				"peak": "峰值 RSS",
				"degraded": "降级记录",
				"history": "任务历史",
				"projects": "项目",
				"delivery": "交付",
				"batches": "批次",
				"registry": "去重注册表",
				"on": "已建立",
				"off": "空",
				"actions": "让 Agent 执行",
				"sent": "已写入新会话草稿",
				"copied": "命令已复制到剪贴板",
				"dispatch-failed": "无法直接派发，已复制命令到剪贴板：",
				"steps": "步数",
				"probed": "探针",
				"renders": "渲染",
				"ok": "通过",
				"bad": "未过",
				"none": "无",
				"yes": "有",
				"generated": "快照时间",
			},
			en: {
				"panel": "CAD Board",
				"scope": "read through session",
				"title": "FreeCAD CAD pipeline",
				"subtitle": "parametric modeling · acceptance gates · memory governor",
				"refresh": "Refresh",
				"loading": "Reading cad/mem/board.json …",
				"unavailable": "The board cannot read cad/mem/board.json yet: this panel reads workspace files through an open session. Open a session, or run cad_freecad board.",
				"governor": "Memory governor",
				"cap": "Per-process cap",
				"floor": "Degrade floor",
				"workers": "Probe worker ceiling",
				"plan": "Next export working point",
				"last": "Last task",
				"peak": "Peak RSS",
				"degraded": "Degrade log",
				"history": "Task history",
				"projects": "Projects",
				"delivery": "Delivery",
				"batches": "Batches",
				"registry": "Dedupe registry",
				"on": "present",
				"off": "empty",
				"actions": "Ask the agent to",
				"sent": "Draft handed to a new session",
				"copied": "Command copied to the clipboard",
				"dispatch-failed": "Could not dispatch directly; copied the command: ",
				"steps": "steps",
				"probed": "probe",
				"renders": "renders",
				"ok": "pass",
				"bad": "fail",
				"none": "none",
				"yes": "yes",
				"generated": "snapshot",
			},
		};
		//#endregion

		//#region styles
		/** Scoped stylesheet, injected once, following the shell's token names. */
		var CSS = [
			".dcp-root{color:var(--dsw-alias-label-primary);font-size:13px;box-sizing:border-box;height:100%;overflow:auto;padding:20px 24px 40px;display:flex;flex-direction:column;gap:14px;max-width:980px}",
			".dcp-root *{box-sizing:border-box}",
			".dcp-head{align-items:flex-start;gap:12px;display:flex;justify-content:space-between}",
			".dcp-title{font-size:18px;font-weight:600;line-height:1.35}",
			".dcp-sub{color:var(--dsw-alias-label-tertiary);font-size:12px}",
			".dcp-card{border:.5px solid var(--dsw-alias-border-l3);border-radius:10px;background:var(--dsw-alias-bg-layer-1);padding:14px 16px;display:flex;flex-direction:column;gap:10px}",
			".dcp-cardH{color:var(--dsw-alias-label-secondary);font-size:11px;text-transform:uppercase;letter-spacing:.04em}",
			".dcp-grid{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;display:grid}",
			".dcp-kv{border-left:2px solid var(--dsw-alias-border-l3);padding-left:8px;display:flex;flex-direction:column;gap:2px;min-width:0}",
			".dcp-k{color:var(--dsw-alias-label-tertiary);font-size:11px}",
			".dcp-v{font-variant-numeric:tabular-nums;font-size:13px;overflow-wrap:anywhere}",
			".dcp-btn{border:.5px solid var(--dsw-alias-border-l3);border-radius:6px;background:transparent;color:var(--dsw-alias-label-primary);cursor:pointer;font-size:12px;padding:5px 10px}",
			".dcp-btn:hover{background:var(--dsw-alias-bg-layer-2)}",
			".dcp-btns{flex-wrap:wrap;gap:6px;display:flex}",
			".dcp-chip{border-radius:4px;font-size:11px;padding:1px 6px}",
			".dcp-ok{background:rgba(52,168,83,.16);color:#3fae63}",
			".dcp-bad{background:rgba(224,86,78,.16);color:#e0604f}",
			".dcp-warn{background:rgba(224,160,60,.16);color:#cf9a3c}",
			".dcp-muted{color:var(--dsw-alias-label-tertiary)}",
			".dcp-row{border-top:.5px solid var(--dsw-alias-border-l3);align-items:center;gap:8px;padding:7px 0;display:flex;flex-wrap:wrap}",
			".dcp-row:first-of-type{border-top:0}",
			".dcp-name{font-weight:550;min-width:110px;overflow-wrap:anywhere}",
			".dcp-tag{color:var(--dsw-alias-label-tertiary);font-size:11px}",
			".dcp-bars{align-items:flex-end;gap:2px;height:34px;display:flex}",
			".dcp-bar{background:var(--dsw-alias-border-l3);border-radius:1px;min-height:2px;width:6px}",
			".dcp-bar.hot{background:#cf9a3c}",
			".dcp-note{color:var(--dsw-alias-label-tertiary);font-size:12px;line-height:1.5}",
			".dcp-toast{border:.5px solid var(--dsw-alias-border-l3);border-radius:6px;background:var(--dsw-alias-bg-layer-2);font-size:12px;padding:6px 10px}",
		].join("\n");
		var cssTagged = false;
		function ensureCss() {
			if (cssTagged || typeof document === "undefined") return;
			var tagId = "@local/dsh-plugin-cad-freecad/board.css";
			if (document.querySelector('style[data-plugin-css="' + tagId + '"]')) {
				cssTagged = true;
				return;
			}
			var tag = document.createElement("style");
			tag.dataset.plugin = "@local/dsh-plugin-cad-freecad";
			tag.dataset.pluginCss = tagId;
			tag.textContent = CSS;
			document.head.appendChild(tag);
			cssTagged = true;
		}
		//#endregion

		//#region helpers
		function num(v, unit) {
			return v === null || v === undefined ? "—" : String(v) + (unit || "");
		}
		function statusChip(t, ok, textOk, textBad) {
			return react.createElement("span", {
				className: "dcp-chip " + (ok ? "dcp-ok" : "dcp-bad"),
			}, ok ? textOk : textBad);
		}
		/**
		 * Text resolver. Deliberately NOT a hook: `apply()` runs outside React,
		 * so this is a plain factory over the injected locale service, with a
		 * built-in dictionary fallback for hosts that do not provide one.
		 */
		function makeText(ctx) {
			var bound = null;
			try {
				if (ctx && ctx.locale && typeof ctx.locale.bind === "function") {
					bound = ctx.locale.bind(NS);
				}
			} catch (error) { /* not fatal: labels below have a fallback */ }
			return function (key) {
				if (bound) {
					try {
						var out = bound(key);
						if (out !== undefined && out !== null && out !== key) return out;
					} catch (error) { /* fall through to the built-in copy */ }
				}
				return DICT.zh[key] || key;
			};
		}
		/**
		 * Newest usable Session id out of the `useSessions` snapshot the slot
		 * owner hands every occupant (`{ phase, ids, byId }`). The board is read
		 * through a Session because workspace files are scoped to one; the
		 * picker never activates a Session, it only names one.
		 */
		function pickSession(snapshot) {
			if (!snapshot || snapshot.phase === "pending") return null;
			var ids = snapshot.ids;
			if (!Array.isArray(ids) || ids.length === 0) return null;
			return String(ids[ids.length - 1]);
		}
		/** Session rows offered in the header, newest last, capped for sanity. */
		function sessionChoices(snapshot, limit) {
			if (!snapshot || !Array.isArray(snapshot.ids)) return [];
			return snapshot.ids.slice(-(limit || 8)).reverse().map(function (id) {
				var row = (snapshot.byId || {})[id] || {};
				return { id: String(id), title: typeof row.title === "string" ? row.title : "" };
			});
		}
		/** Read + parse the board snapshot through the Host file remote. */
		function readBoard(ctx, sessionId) {
			return new Promise(function (resolve, reject) {
				var remote = ctx && ctx.remote;
				var files = remote && remote.workspaceFiles;
				if (!files || typeof files.read !== "function") {
					reject(new Error("no-workspace-files"));
					return;
				}
				var done = false;
				var finish = function (fn, value) {
					if (done) return;
					done = true;
					fn(value);
				};
				Promise.resolve()
					.then(function () {
						// Signature is read(sessionId, path, range, signal); text
						// answers arrive as string or { text | content | data }.
						return files.read(sessionId, BOARD_PATH, undefined, undefined);
					})
					.then(function (res) {
						var text = typeof res === "string" ? res
							: res && (res.text !== undefined ? res.text
								: res.content !== undefined ? res.content
									: res.data);
						if (typeof text !== "string" && text && typeof text.text === "string") text = text.text;
						if (typeof text !== "string") {
							reject(new Error("unexpected-answer"));
							return;
						}
						finish(resolve, JSON.parse(text));
					}, function (error) {
						finish(reject, error);
					});
			});
		}
		//#endregion

		//#region icon
		/** App-bar glyph: a hex prism reading as "solid part" at any size. */
		function CadIcon(props) {
			var size = props.size || 16;
			return react.createElement("svg", {
				width: size, height: size, viewBox: "0 0 24 24", fill: "none",
				stroke: "currentColor", strokeWidth: 1.6, strokeLinejoin: "round",
				"aria-hidden": "true",
			}, react.createElement("path", { d: "M12 3l7 4v10l-7 4-7-4V7l7-4z" }),
				react.createElement("path", { d: "M5 7l7 4 7-4M12 11v10" }));
		}
		//#endregion

		//#region panel
		function BoardCard(props) {
			var t = props.t, board = props.board;
			var gov = board.governor || {};
			var next = gov.next_export || {};
			var last = board.last || {};
			var hist = (board.history || []).slice(0, 24).reverse();
			var peakMax = Math.max.apply(null, [1].concat(hist.map(function (r) {
				return r.peak_mb || 0;
			})));
			var cap = gov.cap_mb || 1;
			return react.createElement(react.Fragment, null,
				react.createElement("section", { className: "dcp-card" },
					react.createElement("div", { className: "dcp-cardH" }, t("governor")),
					react.createElement("div", { className: "dcp-grid" },
						react.createElement("div", { className: "dcp-kv" },
							react.createElement("span", { className: "dcp-k" }, t("cap")),
							react.createElement("span", { className: "dcp-v" }, num(gov.cap_mb, " MB"))),
						react.createElement("div", { className: "dcp-kv" },
							react.createElement("span", { className: "dcp-k" }, t("floor")),
							react.createElement("span", { className: "dcp-v" }, num(gov.floor_mb, " MB"))),
						react.createElement("div", { className: "dcp-kv" },
							react.createElement("span", { className: "dcp-k" }, t("workers")),
							react.createElement("span", { className: "dcp-v" }, num(gov.probe_worker_ceiling, " ×"))),
						react.createElement("div", { className: "dcp-kv" },
							react.createElement("span", { className: "dcp-k" }, t("plan")),
							react.createElement("span", { className: "dcp-v" },
								next.long_side ? next.long_side + "px · " + next.supersample + "× · ≤" + next.max_triangles + " tri" : "—"))),
					last.action ? react.createElement("div", { className: "dcp-row" },
						react.createElement("span", { className: "dcp-name" }, t("last")),
						react.createElement("span", { className: "dcp-tag" }, last.action + (last.project ? " · " + last.project : "")),
						statusChip(t, last.ok !== false, t("ok"), t("bad")),
						react.createElement("span", { className: "dcp-tag" }, t("peak") + " " + num(last.peak_mb, " MB")),
						last.error ? react.createElement("span", { className: "dcp-chip dcp-bad" }, last.error.slice(0, 90)) : null) : null,
					last.degraded && last.degraded.length
						? react.createElement("div", { className: "dcp-note" },
							react.createElement("span", { className: "dcp-chip dcp-warn" }, t("degraded")),
							" " + last.degraded.join(" / "))
						: null),
				hist.length ? react.createElement("section", { className: "dcp-card" },
					react.createElement("div", { className: "dcp-cardH" }, t("history")),
					react.createElement("div", { className: "dcp-bars" }, hist.map(function (r, i) {
						var mb = r.peak_mb || 0;
						return react.createElement("div", {
							key: i,
							title: r.ts + " · " + r.action + " · " + mb + " MB",
							className: "dcp-bar" + (mb > cap ? " hot" : ""),
							style: { height: Math.max(2, Math.round(32 * mb / peakMax)) + "px" },
						});
					})),
					react.createElement("div", { className: "dcp-tag" },
						hist.length + " × · max " + peakMax + " MB / " + cap + " MB")) : null);
		}

		function ProjectsCard(props) {
			var t = props.t, list = props.projects || [];
			return react.createElement("section", { className: "dcp-card" },
				react.createElement("div", { className: "dcp-cardH" }, t("projects") + " · " + list.length),
				list.length === 0 ? react.createElement("div", { className: "dcp-note" }, t("none")) : null,
				list.map(function (p) {
					var rep = p.report || {};
					var prb = p.probe || {};
					return react.createElement("div", { className: "dcp-row", key: p.id },
						react.createElement("span", { className: "dcp-name" }, p.name || p.id),
						react.createElement("span", { className: "dcp-tag" }, p.id),
						rep.ok === undefined ? null : statusChip(t, rep.ok, t("ok"), t("bad")),
						rep.steps ? react.createElement("span", { className: "dcp-tag" }, rep.steps + " " + t("steps")) : null,
						typeof prb.probed === "number" ? react.createElement("span", { className: "dcp-tag" }, t("probed") + " " + prb.probed + (prb.failed ? " ✗" + prb.failed : "")) : null,
						p.renders && p.renders.count ? react.createElement("span", { className: "dcp-tag" }, t("renders") + " " + p.renders.count + " × " + ((p.renders.views || [])[0] || {}).w + "px") : null,
						rep.failed && rep.failed.length ? react.createElement("span", { className: "dcp-chip dcp-bad" }, rep.failed.join(",")) : null,
						rep.warns && rep.warns.length ? react.createElement("span", { className: "dcp-chip dcp-warn" }, rep.warns.join(",")) : null);
				}));
		}

		function DeliveryCard(props) {
			var t = props.t, d = props.delivery || {};
			var batches = d.batches || [];
			return react.createElement("section", { className: "dcp-card" },
				react.createElement("div", { className: "dcp-cardH" }, t("delivery")),
				react.createElement("div", { className: "dcp-row" },
					react.createElement("span", { className: "dcp-name" }, t("registry")),
					react.createElement("span", { className: "dcp-tag" }, d.registered ? t("on") : t("off"))),
				batches.map(function (b) {
					return react.createElement("div", { className: "dcp-row", key: b.name },
						react.createElement("span", { className: "dcp-name" }, b.name),
						react.createElement("span", { className: "dcp-tag" }, (b.projects || []).length + " " + t("projects")),
						b.csv ? react.createElement("span", { className: "dcp-chip dcp-ok" }, "info.csv") : null,
						b.staging ? react.createElement("span", { className: "dcp-chip dcp-warn" }, ".staging") : null,
						react.createElement("span", { className: "dcp-tag" }, b.updated_at || ""));
				}));
		}

		function CadPanel(props) {
			var ctx = props.ctx, t = props.t, run = props.run;
			var stBoard = react.useState(null);
			var loaded = stBoard[0], setBoard = stBoard[1];
			// `board` may be supplied by the owner (or a test); otherwise it is
			// read from the workspace snapshot below.
			var board = props.board === undefined ? loaded : props.board;
			var stNotice = react.useState("");
			var notice = stNotice[0], setNotice = stNotice[1];
			var stTick = react.useState(0);
			var tick = stTick[0], setTick = stTick[1];
			// The board is read through a Session (workspace files are Session
			// scoped). `useSessions` is one of the standard props the slot owner
			// gives every occupant; without it there is nothing to read.
			var snapshot = null;
			if (typeof props.useSessions === "function") {
				try {
					snapshot = props.useSessions(function (s) { return s; });
				} catch (error) {
					snapshot = null;
				}
			}
			var chosen = react.useState("");
			var sessionOverride = chosen[0], setSessionOverride = chosen[1];
			var sessionId = sessionOverride || pickSession(snapshot);
			var choices = sessionChoices(snapshot, 12);

			react.useEffect(function () {
				ensureCss();
			}, []);

			react.useEffect(function () {
				if (props.board !== undefined) return undefined;
				var alive = true;
				setBoard(null);
				if (!sessionId) {
					setBoard({ kind: "unavailable" });
					return undefined;
				}
				var load = function () {
					// No point reading the workspace while the page is hidden.
					if (typeof document !== "undefined" && document.hidden) return;
					readBoard(ctx, sessionId).then(function (data) {
						if (alive) setBoard(data);
					}, function () {
						if (alive) setBoard({ kind: "unavailable" });
					});
				};
				load();
				var timer = setInterval(load, POLL_MS);
				return function () {
					alive = false;
					clearInterval(timer);
				};
			}, [ctx, tick, sessionId, props.board]);

			var cards = !board ? react.createElement("div", { className: "dcp-note" }, t("loading"))
				: board.kind === "unavailable" ? react.createElement("div", { className: "dcp-note" }, t("unavailable"))
					: react.createElement(react.Fragment, null,
						react.createElement(BoardCard, { t: t, board: board }),
						react.createElement(ProjectsCard, { t: t, projects: board.projects }),
						react.createElement(DeliveryCard, { t: t, delivery: board.delivery }));

			return react.createElement("div", { className: "dcp-root" },
				react.createElement("header", { className: "dcp-head" },
					react.createElement("div", null,
						react.createElement("div", { className: "dcp-title" }, t("title")),
						react.createElement("div", { className: "dcp-sub" }, t("subtitle"),
							board && board.generated_at ? " · " + t("generated") + " " + board.generated_at : "")),
					react.createElement("div", { className: "dcp-btns" },
						choices.length > 1 ? react.createElement("select", {
							className: "dcp-btn",
							value: sessionId || "",
							title: t("scope"),
							onChange: function (event) { setSessionOverride(event.target.value); },
						}, choices.map(function (row) {
							return react.createElement("option", { key: row.id, value: row.id },
								row.title || row.id);
						})) : null,
						react.createElement("button", {
							className: "dcp-btn", type: "button",
							onClick: function () { setTick(tick + 1); },
						}, t("refresh")))
					),
				cards,
				react.createElement("section", { className: "dcp-card" },
					react.createElement("div", { className: "dcp-cardH" }, t("actions")),
					react.createElement("div", { className: "dcp-btns" }, ACTIONS.map(function (a) {
						return react.createElement("button", {
							key: a.id, className: "dcp-btn", type: "button",
							onClick: function () {
								Promise.resolve(run(a)).then(function (label) {
									setNotice(label);
								}, function (error) {
									setNotice(t("dispatch-failed") + String(error && error.message || error));
								});
							},
						}, a.id);
					})),
					notice ? react.createElement("div", { className: "dcp-toast" }, notice) : null));
		}
		//#endregion

		//#region plugin
		/*
		 * Services this bundle needs. A `remote.<face>` must be named here:
		 * remote sub-faces are materialised on demand, so reading
		 * `ctx.remote.workspaceFiles` without declaring it yields undefined.
		 */
		var inject = ["slots", "locale", "remote", "remote.workspaceFiles", "uiWorkspace"];

		/**
		 * Contribute the CAD entry to the app bar plus the panel it opens.
		 * @param {any} ctx - browser plugin context.
		 */
		function apply(ctx) {
			ctx.effect(function () {
				try {
					return ctx.locale.register(NS, DICT);
				} catch (error) {
					return undefined;
				}
			}, "cad-freecad: dictionaries");
			var t = makeText(ctx);

			/**
			 * Hand one action to the agent. `uiWorkspace.startSession` accepts a
			 * plain-text draft, so the user lands on a composed prompt rather
			 * than on a half-typed command; the clipboard is the last resort.
			 */
			var run = function (action) {
				var text = action.prompt;
				try {
					if (ctx.uiWorkspace && typeof ctx.uiWorkspace.startSession === "function") {
						ctx.uiWorkspace.startSession(undefined, {
							prompt: text,
							whenDraftExists: "replace",
						});
						return Promise.resolve(t("sent"));
					}
				} catch (error) { /* fall through to the clipboard */ }
				if (typeof navigator !== "undefined" && navigator.clipboard) {
					return navigator.clipboard.writeText(text).then(function () {
						return t("copied");
					});
				}
				return Promise.reject(new Error("no dispatch channel"));
			};

			ctx.slots.inject("sidebar.panellist", function () {
				return ctx.slots.register({
					name: "sidebar.panellist",
					id: PANEL_ID,
					order: 20,
					label: function () { return t("panel"); },
				}, CadIcon);
			});
			ctx.slots.inject("main", function () {
				return ctx.slots.register({
					name: "main",
					key: PANEL_ID,
					inject: function () {
						return { ctx: ctx, t: t, run: run };
					},
				}, CadPanel);
			});
		}
		//#endregion

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	},
});
