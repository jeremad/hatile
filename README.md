# Tile (local) for Home Assistant

A Home Assistant integration that makes Tile trackers ring over Bluetooth, from
Home Assistant's own Bluetooth adapter, without going through the Tile servers.

It started as a port of [jeretile](https://github.com/jeremad/jeretile) to
Python. It implements only the part of Tile's Bluetooth protocol needed to
authenticate and play the find song. Tile does not support this use: use at your
own risk.

## What you get

One device per Tile, with:

- **Ring** and **Stop ringing** buttons. Put them on a dashboard to ring the
  Tile from Home Assistant or its mobile app.
- A **Ring duration** setting, from once to until stopped, used by the Ring
  button and kept across restarts. There is no volume setting: the protocol
  carries one, but Tile's reference firmware and the Tiles tried so far play
  every volume alike.
- **Presence**: on while Home Assistant hears the Tile's Bluetooth
  advertisements. It turns off within 5 minutes of the Tile leaving, or up to
  20 minutes while Home Assistant has not yet heard enough advertisements to
  learn how often the Tile sends them. A Tile connected to a phone may stop
  advertising and look away.
- **Signal strength** (RSSI) of the last advertisement heard, updated every
  30 seconds. It is a diagnostic sensor.

Presence and signal strength only listen: they never connect to the Tile, so
they cost it no battery.

## Requirements

- Home Assistant 2026.3 or later, with a working Bluetooth adapter (the
  `bluetooth` integration).
- The Tile within Bluetooth range of that adapter, roughly 10 m indoors.
- The Tile not connected to a phone when you press the button: it accepts one
  connection at a time.
- The Tile's Bluetooth MAC address and auth key (see below).

## Installation

Copy `custom_components/hatile` into the `custom_components` folder of your
Home Assistant configuration, then restart Home Assistant.

With HACS, add this repository as a custom repository of type *Integration*
instead.

## Configuration

*Settings → Devices & services → Add integration → Tile (local)*, then enter:

- **Name**: shown in Home Assistant, for example `Wallet`.
- **Bluetooth MAC address**: for example `AA:BB:CC:DD:EE:FF`. An Android BLE
  scanner such as nRF Connect shows it. iOS and macOS hide MAC addresses.
- **Auth key**: the 16-byte key of the Tile, in base64.

The auth key can be retrieved once with [pytile](https://github.com/bachya/pytile):

```python
import asyncio

from aiohttp import ClientSession

from pytile import async_login


async def main() -> None:
    async with ClientSession() as session:
        api = await async_login("<email>", "<password>", session)
        tiles = await api.async_get_tiles()
        for tile in tiles.values():
            print(tile.name, tile._tile_data["result"]["auth_key"])


asyncio.run(main())
```

Keep the auth key secret: anyone who has it can ring the Tile.

## Troubleshooting

When the Tile does not ring, the error says why, for example `wrong auth key`.
To see every message exchanged with the Tile, enable debug logs in
`configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.hatile: debug
```

## Development

The development environment is managed with [Poetry](https://python-poetry.org/)
and needs Python 3.14.2 or later, like Home Assistant. Install it, in `.venv`:

```bash
poetry install
```

It holds Home Assistant, its test harness and the requirements of its
Bluetooth integration. Home Assistant provides those at runtime: the
integration itself has no requirements.

The protocol lives in `custom_components/hatile/tile_protocol.py`, with no Home
Assistant dependency. It is a client for TOA (Tile Over-the-Air), the protocol
Tile trackers speak over Bluetooth LE. Its sources:

- `toa.h`, Tile's own header, shipped in
  [Nordic's nRF5 SDK 16](https://developer.nordicsemi.com/nRF5_SDK/nRF5_SDK_v16.x.x/)
  under `external/tile/tile_lib/src/toa/`: command and response codes, message
  formats, error codes and the authentication check.
- `tile_song_module.h`, in the same SDK: song numbers, strengths and durations.
- [node-tile](https://github.com/lesleyxyz/node-tile), reverse-engineered from
  the Tile app: session key, MIC, and the song and keep-alive sub-commands. The
  stop sub-command is inferred from node-tile's response codes.

The tests run the protocol against a simulated Tile, and the integration
inside Home Assistant with a mocked Bluetooth stack:

```bash
poetry run pytest
```

The simulated Tile follows those sources, not real hardware: test changes on a
real Tile too, from a computer, with the same protocol code:

```bash
poetry run python scripts/ring.py AA:BB:CC:DD:EE:FF
```

It asks for the auth key, then rings the Tile; `--stop` stops it instead.

The code is linted and formatted with [ruff](https://docs.astral.sh/ruff/):

```bash
poetry run ruff check --fix
```

```bash
poetry run ruff format
```

GitHub Actions runs the same checks and the tests on every pull request and
every push to `main`, from `.github/workflows/ci.yml`.

The icon Home Assistant shows for the integration is in
`custom_components/hatile/brand/`, drawn from the SVG files in `assets/`:
after changing one, render it again to its two PNG files, at 256 and 512 px.

Home Assistant's version comes from
[pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):
when updating it, update the Bluetooth requirements in `pyproject.toml` to
the versions of that Home Assistant release.
