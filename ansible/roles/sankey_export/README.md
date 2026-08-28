# sankey_export

Opt-in exporter that watches a Finanzfluss budget workbook in Nextcloud
and republishes a Sankey-ready export from it (`sankey_export_enabled`,
default `false`).

> Julian is planning to restructure this away from Finanzfluss toward
> something more future-proof -- parked for now, not started.

- Runs a Python service (not a container) as a dedicated non-admin
  Nextcloud account, the same shape as `nextcloud_tools`.
- Polls cheaply and often (`sankey_export_poll_interval`, default 1 min)
  with a PROPFIND-only ETag check; the expensive Playwright-driven export
  only runs on the rare tick where the workbook
  (`sankey_export_workbook_name`) actually changed.
- Output lands in `sankey_export_output_dir`.

Bootstrap with `ansible-playbook ansible/playbooks/sankey-export.yml`
(creates the dedicated account, requires the exact confirmation string
`BOOTSTRAP_SANKEY_EXPORT`).
