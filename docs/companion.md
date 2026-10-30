# Companion desktop route (historical)

The experimental browser workbench moved to **GNU Radio World** on
`feature/gnuradio-world`. See [launch instructions](../README.md) and
[integration details](gnuradio-world.md). The desktop-streaming server,
noVNC launcher and desktop container have been removed from this branch.

GNU Radio Companion's native compiler remains the deployment code generator.
The optional host-native UI is still available to developers who have installed
GNU Radio, GTK and PyQt5:

```bash
bash scripts/start_companion_native.sh --workspace ./my-native-skill --transmit
```

Use a separate workspace for that interface. The old implementation and its
qualification evidence remain in Git history on `feature/gnuradio-companion`.
