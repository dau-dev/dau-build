# design config group

`design=designs/<category>/<name>` selects a design — an assortment of tiles
to synthesize (e.g. `designs/custom/aggregator_heavy`). The tile-composition
models are private (dau/dau-core own `ScanCompositionSpec`); dau (private)
registers its own `design/` configs on the search path via its own
`hydra.lernaplugins` entry point, extending this generic build framework
without dau-build depending on the private core. This directory is the
public scaffold for that extension point.
