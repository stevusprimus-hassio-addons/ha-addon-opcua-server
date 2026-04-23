# Home Assistant Add-on Repository: OPC UA Server

This repository contains a Home Assistant **Add-on** that runs an **OPC UA server** and exposes selected Home Assistant entity states as OPC UA variables.

## Add-ons

- `opcua-server`: OPC UA server that mirrors configured Home Assistant entities.

## Installation (Custom repository)

1. Home Assistant → **Settings** → **Add-ons** → **Add-on Store**.
2. Open the menu (top right) → **Repositories**.
3. Add this repository URL.
4. Install the `OPC UA Server` add-on.

## Usage

Configure the add-on with:

- `endpoint`: OPC UA endpoint URL (default `opc.tcp://0.0.0.0:4840/ha/`)
- `namespace_uri`: OPC UA namespace URI
- `entities`: list of Home Assistant entity IDs to expose

The add-on connects to Home Assistant via the Supervisor-provided WebSocket endpoint and token.

## Security

Initial version runs without OPC UA TLS/security policies. TLS/encryption can be added later.
