import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp
from asyncua import Server, ua


@dataclass
class AddonConfig:
    endpoint: str
    namespace_uri: str
    server_name: str
    entities: List[str]


def _read_options() -> Dict[str, Any]:
    # Home Assistant add-ons expose options at /data/options.json
    with open("/data/options.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> AddonConfig:
    opts = _read_options()
    return AddonConfig(
        endpoint=opts.get("endpoint", "opc.tcp://0.0.0.0:4840/ha/"),
        namespace_uri=opts.get("namespace_uri", "urn:homeassistant:opcua"),
        server_name=opts.get("server_name", "HomeAssistant OPC UA"),
        entities=list(opts.get("entities", [])),
    )


def supervisor_ws_url() -> str:
    # Supervisor provides an internal WS endpoint for HA Core
    # Commonly: ws://supervisor/core/websocket
    return os.environ.get("SUPERVISOR_WS_URL", "ws://supervisor/core/websocket")


def supervisor_token() -> str:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is not set; ensure hassio_api/homeassistant_api are enabled")
    return token


async def ha_ws_listen(
    session: aiohttp.ClientSession,
    ws_url: str,
    token: str,
    entities: List[str],
    on_state: "callable",
) -> None:
    async with session.ws_connect(ws_url, autoping=True, heartbeat=30) as ws:
        # auth
        msg = await ws.receive_json()
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected WS message: {msg}")
        await ws.send_json({"type": "auth", "access_token": token})
        msg = await ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"Auth failed: {msg}")

        # subscribe to state_changed
        req_id = 1
        await ws.send_json(
            {
                "id": req_id,
                "type": "subscribe_events",
                "event_type": "state_changed",
            }
        )
        sub_resp = await ws.receive_json()
        if not sub_resp.get("success"):
            raise RuntimeError(f"subscribe_events failed: {sub_resp}")

        entity_set = set(entities)

        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            if data.get("type") != "event":
                continue
            event = data.get("event") or {}
            if event.get("event_type") != "state_changed":
                continue
            ev_data = event.get("data") or {}
            entity_id = ev_data.get("entity_id")
            if not entity_id or entity_id not in entity_set:
                continue
            new_state = (ev_data.get("new_state") or {}).get("state")
            await on_state(entity_id, new_state)


def coerce_variant(value: Optional[str]) -> ua.Variant:
    if value is None:
        return ua.Variant(None, ua.VariantType.Null)

    # Try numeric
    try:
        if "." in value:
            return ua.Variant(float(value), ua.VariantType.Double)
        return ua.Variant(int(value), ua.VariantType.Int64)
    except Exception:
        pass

    # booleans
    if value.lower() in ("true", "false"):
        return ua.Variant(value.lower() == "true", ua.VariantType.Boolean)

    # fallback string
    return ua.Variant(value, ua.VariantType.String)


def sanitize_browse_name(entity_id: str) -> str:
    # OPC UA browse names cannot contain some characters; keep it simple.
    return entity_id.replace(".", "_").replace(" ", "_")


async def main() -> None:
    cfg = load_config()

    server = Server()
    await server.init()
    server.set_endpoint(cfg.endpoint)
    server.set_server_name(cfg.server_name)

    idx = await server.register_namespace(cfg.namespace_uri)

    objects = server.nodes.objects
    ha_obj = await objects.add_object(idx, "HomeAssistant")

    # Create variables for each entity
    var_nodes: Dict[str, Any] = {}
    for ent in cfg.entities:
        browse = sanitize_browse_name(ent)
        node = await ha_obj.add_variable(idx, browse, ua.Variant("unknown", ua.VariantType.String))
        await node.set_writable(False)
        var_nodes[ent] = node

    async def on_state(entity_id: str, state: Optional[str]) -> None:
        node = var_nodes.get(entity_id)
        if not node:
            return
        await node.write_value(coerce_variant(state))

    async with server:
        async with aiohttp.ClientSession() as session:
            await ha_ws_listen(
                session=session,
                ws_url=supervisor_ws_url(),
                token=supervisor_token(),
                entities=cfg.entities,
                on_state=on_state,
            )


if __name__ == "__main__":
    asyncio.run(main())
