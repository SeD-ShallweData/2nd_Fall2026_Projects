# STEP 3 UI and STEP 4 handoff

The service keeps Python + plain HTML/CSS/JavaScript. No frontend build or package
installation is required. This repository is a reviewed source copy; private inputs,
completed caches and credentials must be provisioned separately under the existing
data contracts. Read `REPRODUCIBILITY.md` and `DATA_AND_RIGHTS.md` first.

## Pages and state

Routes: `#/overview`, `#/signals`, `#/activity`, `#/welfare`, `#/agent`.
The default is Overview. Browser back/forward and reload restore the route.
City/dong context is stored in tab-local sessionStorage (`bbigeudeok.context.v1`).
SK month/experiment, welfare filters and Agent filters are independent. ROA's aggregate
period is never taken from the SK month selector. No questions or answers are stored.

Existing analysis limitation: the SK catalog exposes six age groups with male/female
values already combined. A separate sex selector is absent in the original API and
was not introduced by this UI change. Adding it would require a separately reviewed
analysis/cache/API extension; do not present it as an existing completed feature.

`dashboard.js` owns routing, shared context and successful read-request memoization.
`roa_dashboard.js` owns the regional map/evidence and grounded Agent interface.
`activity_explorer.js` retains SK map, drill-down and chart operations.
`welfare_explorer.js` owns paginated search and the resource reader.
`dashboard.css` centralizes design tokens and responsive layouts.

The HTML constructs page shells once. SK Leaflet map creation and the local Plotly
bundle load happen only on first activity entry; the ROA map loads on signals entry;
welfare requests happen on welfare entry/condition changes; Agent only submits when
the user sends a question. Reentry reuses maps and calls invalidateSize/Plotly resize.
Failed reads are retryable. Request generations discard outdated region/search/detail/
Agent responses. Pending Agent requests block a second send, including after edits.

## Server and static resources

Entry point: `Code/sk_admin_mapping/run_dashboard.py --port 8766`.
Use `--structured` to disable actual provider calls. This launcher opts in to the
existing verified welfare API cache. It does not refresh the Seoul API.
Run with the existing private environment's Python and preserve the current working
data/cache locations. Public clone alone cannot render real observations.

The server binds to loopback. Page entry `/` or `/index.html` is rendered by
`spatial_explorer.page_html()` with the existing Folium head/body/script placeholders.
Static allowlist: `/dashboard.css`, `/dashboard.js`, `/roa_dashboard.js`,
`/activity_explorer.js`, `/welfare_explorer.js`, plus existing `/plotly.js`.
This does not expose arbitrary source files, env files, contracts or cached datasets.
UI assets retain no-store; existing Plotly private caching is unchanged.

Existing API contracts remain: `/api/catalog`, `/api/map`, `/api/detail`,
`/api/roa/catalog`, `/api/roa/map`, `/api/roa/detail`, `/api/roa/welfare`,
`/api/roa/resource`, `/api/agent/status`, `/api/service/status`, `POST /api/agent`.
The browser never receives provider keys. Structured remains the default selection;
Upstage is enabled in the UI only after server status reports ready, and a successful
provider response is distinguished from fallback. The existing backend validates
tool calls, observations, welfare records and response content.

Keep the existing LLM/Seoul environment variable names in `llm.env.example` /
`seoul.env.example` and the repository's configuration documentation; configure them
server-side only. `ROA_ENABLE_LLM`, `ROA_USE_WELFARE_API_CACHE` are launcher flags.
Do not change provider request budgets or automatically refresh external data.

## Verification and publication

`python -B -m unittest discover -s BbiGeuDeok/tests -v` runs public synthetic tests.
`node --test BbiGeuDeok/tests/test_ui_state.cjs` runs 17 dependency-free UI state tests.
They contain synthetic inputs and do not prove private-data or live-provider behavior.
The private STEP 3 report holds the real-data/browser evidence and screenshots.
Screenshots, logs, local audit tools and private results are excluded from this copy.

Before future commit/push, review the diff, stage only approved files, then run
`python -B BbiGeuDeok/scripts/check_public.py` against the actual index.
This STEP changes working files and the public manifest only; it does not stage,
commit, push or deploy. Other teams' directories are outside its scope.

STEP 4 still needs the existing data redistribution review, welfare-cache cold-start
memory work, production server/security design, authentication and persistent request
budgets. UI lazy loading reduces browser work; it does not solve Python's cache
validation peak memory. Existing large caches were not repacked or regenerated.

## Independent review and presentation palette

The question composer precedes the Agent response, so a long answer does not push
the next question to the bottom. The response includes a return-to-question button.
Region, review date and resource filters still invalidate previous responses.

Regional evidence tables round display values to two decimals. Cell descriptions
retain original precision. ON/OFF/HOLD and above-median decisions remain server
values; equal rounded text can have different threshold results.

The resource review discloses missing-detail and ID-conflict counts from the current
API metadata, separately from confirmed availability. These counts are not hardcoded.
Unknown API completeness is shown as unknown in a local-only response.

The team's presentation inspired the light gray page background and subtle mint/
peach radial gradients. Cards, inputs, maps and charts keep white surfaces. R/O/A
label accents are blue/green/orange; map legends, chart colors and analysis semantics
are unchanged. No slide imagery, fonts, new library or paid resource is bundled.

The four added synthetic tests cover equal rounded values with differing original
values, HOLD preservation, metadata count distinctions and unknown completeness.
Browser/private-data/live-provider evidence remains excluded from the public copy.

For chart cards below 480px, the legend moves below the plot and is limited to
80px with Plotly's built-in scrolling for many series. Resizing restores the
original layout on wider cards. Only layout attributes change; figure data and
series colors remain those returned by the existing API.
