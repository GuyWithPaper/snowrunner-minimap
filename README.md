# SnowRunner Minimap

An always-on-top minimap overlay for SnowRunner (PC). It follows your truck live and shows the
region the way the in-game map does: terrain, every tree, mud, water, buildings and props, the
game's map icons (garages, fuel, watchtowers, tasks, contests, upgrades) and your own fog of war.

Everything is built on your PC from your installed copy of the game. No game files ship with the app.

## Install
Download `SnowRunnerMinimap-Setup.exe` from the latest [release](../../releases/latest) and run it.
The app updates itself when a new release is published.

## Use
Start SnowRunner (play in **Borderless** or **Windowed** mode so overlays can draw on top), then drive.
The first time you visit a region the map is drawn from the game files (about a minute).

| Keys | Action |
|---|---|
| Ctrl+Shift+M | show / hide |
| Ctrl+Shift+F | big map (whole region) |
| Ctrl+Shift+ = / - | zoom |
| Ctrl+Shift+R | rotate with truck / north up |
| Ctrl+Shift+G | fog of war on / off |
| Ctrl+Shift+U | unlock to move (drag) and resize (mouse wheel), press again to lock |
| Ctrl+Shift+Q | quit |

The tray icon has the same options plus *Check for updates*.

## How it works
- Truck position: read-only access to the game process. `TRUCK_CONTROL` is found by its RTTI class name, so it survives game updates.
  The truck's Havok rigid-body transform gives position and heading. Nothing is written to the game.
- Map: heightmap, terrain material blend, tint, mud, and water from the level `.pak` files; plant instances from `data.stg`;
  landmark meshes (buildings, bridges, props) and map markers from `data_for_ssl.bin`; colors from the game's textures;
  lit with the game's `navigation_map` sun/ambient/tonemap settings and its `env_lut_minimap` color LUT.
- Fog of war: your own `fog_level_*.cfg` save files, refreshed while you play.
- Map detection: terrain height under the truck is matched against every region.

Mesh parsing is vendored from [SnowrunnerMapConverter](https://github.com/skibidius/SnowrunnerMapConverter) (MIT).

## Releasing a new version (maintainer)
```powershell
.\release.ps1 1.0.1 -Publish -Notes "What changed"
```
This builds the app (PyInstaller) and installer (Inno Setup), then creates a GitHub release. Installed copies pick it up on next start, or within 4 hours.
