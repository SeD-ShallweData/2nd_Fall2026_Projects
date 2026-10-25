# STEP 3 UI references and asset provenance

Reviewed 2026-10-25. The dashboard uses original plain HTML, CSS and JavaScript.

| Reference | Use | License / shipped assets |
|---|---|---|
| [Tabler layout documentation](https://docs.tabler.io/ui/layout) | Sidebar navigation, page header, form/table hierarchy | Visual reference only; no Tabler package, template, code, font or icon asset copied |
| [Tabler core license](https://github.com/tabler/tabler/blob/dev/LICENSE) | Checked the free core's MIT terms | Separate third-party plugins are not covered by this assumption; none added |
| [shadcn/ui sidebar](https://ui.shadcn.com/docs/components/base/sidebar) | Spacing and information hierarchy | Visual reference only; no React, generated components or paid blocks added |

No new CDN, chart library, font download, image or paid resource is introduced.
Existing Folium-generated Leaflet dependencies, OpenStreetMap tiles and attribution remain.
Plotly continues to be served from the installed Python package at `/plotly.js`.
The existing project's `LICENSE_NOTICE.md` still governs original project sources;
consulting an MIT reference does not relicense the project or its datasets.

Design tokens live in `Code/sk_admin_mapping/dashboard.css`: navy brand, teal action,
white surfaces, light gray background and borders, distinct warning/error text.
System Korean fonts, native form elements, visible keyboard focus, skip navigation,
ARIA state, text plus color/dash legends and narrow-screen layout are used throughout.

The team's final presentation was inspected locally for the independent STEP 3
review. Its light gray (`#F4F4F4`) background, white cards and diffused mint/peach
accents informed the CSS palette. Presentation contents, slide layouts, 3D imagery
and fonts are not bundled or reproduced. `--mint-glow`, `--peach-glow` and
`--axis-r/o/a` centralize these accents. Gradients apply to the workspace background
and introduction only; maps, charts, forms and responses retain their data colors
and opaque surfaces. No additional license or redistribution right is implied.
