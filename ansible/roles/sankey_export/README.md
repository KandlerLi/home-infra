# sankey_export

Opt-in exporter that watches a Finanzfluss budget workbook in Nextcloud
and republishes a Sankey diagram rendered from it (`sankey_export_enabled`,
default `false`).

The workbook is still Finanzfluss' own free downloadable budget
template (same "Einnahmen"/"Ausgaben" sheet layout as ever -- that's
just a spreadsheet, not a live dependency). **The diagram itself is
rendered locally with Plotly/Kaleido as of 2026-09-06**, not by driving
a browser against finanzfluss.de and scraping their own Highcharts
export -- that scrape was the actual fragility (their site changing its
export flow, rate-limiting harder, or adding bot detection could break
it with zero warning), and removing it was the whole point of finally
picking this parked item back up.

- Runs a Python service (not a container) as a dedicated non-admin
  Nextcloud account, the same shape as `nextcloud_tools`.
- Polls cheaply and often (`sankey_export_poll_interval`, default 1 min)
  with a PROPFIND-only ETag check; the render only runs on the rare
  tick where the workbook (`sankey_export_workbook_name`) actually
  changed.
- Kaleido still needs a real Chromium-family browser to drive via CDP
  (it doesn't draw the chart itself either) -- `apt install chromium`
  during provisioning, pointed to explicitly via the service unit's own
  `BROWSER_PATH` env var rather than letting Kaleido manage a private
  copy.
- Output lands in `sankey_export_output_dir`, same filename
  (`finanzfluss-diagramm.png`) as before the rendering change, so any
  existing Nextcloud share link/embed keeps working.

Bootstrap with `ansible-playbook ansible/playbooks/sankey-export.yml`
(creates the dedicated account, requires the exact confirmation string
`BOOTSTRAP_SANKEY_EXPORT`).
