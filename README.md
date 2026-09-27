# Factorio-Achievement-Unlocker

Factorio-Achievement-Unlocker prepares a **separate executable copy** with changes to the achievement checks used when mods are active. It supports Windows and Linux x86-64 builds. The original `factorio` or `factorio.exe` is never modified. The tool runs locally and does not download code. Write access to the output directory is required.

[Russian version](README.ru.md)

> Compatibility depends on the exact Factorio build. Every signature must match the expected number of times before an output is written. The included profiles are based on earlier community research; they have not been validated against a current game binary in this workspace.

## Quick start

Install Python 3.10 or newer. From this repository:

```sh
python -m pip install -e .
factorio-achievement-unlocker inspect "/path/to/Factorio/bin/x64/factorio"
factorio-achievement-unlocker prepare "/path/to/Factorio/bin/x64/factorio"
factorio-achievement-unlocker launch "/path/to/Factorio/bin/x64/factorio"
```

On Windows, use the full path to `factorio.exe`:

```powershell
factorio-achievement-unlocker prepare "C:\Program Files (x86)\Steam\steamapps\common\Factorio\bin\x64\factorio.exe"
```

The default output is `factorio-unlocked` or `factorio-unlocked.exe` beside the original. Use `--output PATH` to choose another location. `launch` refreshes the managed copy after a game update and starts it. Pass game arguments with repeated `--game-arg=VALUE` options.

The default `--data modded` keeps the game's separate achievement data for modded play. Use `--data vanilla` only if you deliberately want the regular achievement data file. Switching modes rebuilds the managed copy.

To remove a prepared copy:

```sh
factorio-achievement-unlocker restore "/path/to/Factorio/bin/x64/factorio-unlocked"
```

On Windows, add `.exe` to that path. `restore` checks the copy against its recorded SHA-256 and refuses to remove a file changed by another program. It never removes the original executable.

## Safety checks

1. The tool verifies the PE or ELF format and x86-64 architecture. It searches only executable sections or segments.
2. Every enabled signature must have its expected match count. The original bytes at each edit are checked, and overlapping edits are rejected.
3. All edits are applied in memory and verified. The completed copy is written to a temporary file before an atomic replacement.
4. A JSON record beside the copy stores the source and output hashes. Interrupted updates can be resumed, and removal is guarded by a hash check.

The tool does not change saves or bypass achievement restrictions caused by cheats or console commands. Reproduce crashes with the original game executable before reporting them to the game developer.

## Current limitations

- A Factorio update may invalidate the signatures. An `unsupported` error means a new, verified profile is required; there is no force option.
- Steam launch behavior and Steam achievement delivery have not been tested on this machine. Launch the prepared copy within the Steam environment and verify with a test save first.
- Steam launch options may need a wrapper script. The CLI accepts game arguments, but does not rewrite Steam's `%command%` expansion.
- Windows uses a separate `.exe`, rather than a DLL loader. If a particular Factorio build requires the original executable name, use a different launch arrangement; this tool will not overwrite the original automatically.

## Development

```sh
python -m unittest discover -s tests -v
```

The implementation and repository layout are newly written. [ATTRIBUTION.md](ATTRIBUTION.md) records the origin of the byte signatures and the original license notices. This is an independent community project and is not affiliated with Wube Software.
