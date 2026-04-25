import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import fnmatch

import aiohttp
from asyncua import Server, ua


@dataclass
class AddonConfig:
    endpoint: str
    namespace_uri: str
    server_name: str
    entities: List[str]
    log_level: str = "info"


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
        log_level=str(opts.get("log_level", "info")),
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


def _normalize_entities(raw: List[str]) -> Tuple[List[str], List[str]]:
    """Normalize entity IDs / wildcard patterns from config.

    Supports glob-style '*' wildcards (no regex).

    Returns (normalized_patterns, warnings).
    """
    warnings: List[str] = []
    normalized: List[str] = []
    seen: Set[str] = set()

    for item in raw:
        if item is None:
            continue
        ent = str(item).strip().lower()
        if not ent:
            continue
        if " " in ent:
            warnings.append(f"Entity '{item}' contains spaces; did you mean '{ent.replace(' ', '')}'?")
        if "." not in ent:
            warnings.append(f"Entity '{item}' does not look like an entity_id/pattern (expected 'domain.object_id')")
        if any(ch in ent for ch in ("?", "[", "]")):
            warnings.append(
                f"Pattern '{item}' contains glob characters other than '*'; only '*' is supported and others will be treated literally"
            )
        if ent in seen:
            warnings.append(f"Duplicate entry '{ent}' removed")
            continue
        seen.add(ent)
        normalized.append(ent)

    if not normalized:
        warnings.append("No entities configured; OPC UA server will start but expose no variables")

    return normalized, warnings


async def ha_ws_get_states(session: aiohttp.ClientSession, ws_url: str, token: str) -> Dict[str, Any]:
    """Fetch current HA states via websocket (get_states)."""
    async with session.ws_connect(ws_url, autoping=True, heartbeat=30) as ws:
        msg = await ws.receive_json()
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected WS message: {msg}")
        await ws.send_json({"type": "auth", "access_token": token})
        msg = await ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"Auth failed: {msg}")

        await ws.send_json({"id": 1, "type": "get_states"})
        resp = await ws.receive_json()
        if not resp.get("success"):
            raise RuntimeError(f"get_states failed: {resp}")

        states = resp.get("result") or []
        return {s.get("entity_id"): s for s in states if s.get("entity_id")}


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


_LOG_LEVELS: Dict[str, int] = {
    "trace": logging.DEBUG,  # Python stdlib has no TRACE; map to DEBUG
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def _setup_logging(level: str) -> None:
    lvl = _LOG_LEVELS.get((level or "").strip().lower(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s [%(levelname)s] [opcua-server] %(message)s",
    )


async def main() -> None:
    cfg = load_config()
    _setup_logging(cfg.log_level)
    log = logging.getLogger("opcua-server")

    log.info("Starting OPC UA Server")
    log.info("Endpoint: %s", cfg.endpoint)
    log.info("Namespace URI: %s", cfg.namespace_uri)
    log.info("Server name: %s", cfg.server_name)
    log.info("Configured entities/patterns: %s", ", ".join(cfg.entities) if cfg.entities else "<none>")

    # Normalize configured entity IDs / wildcard patterns
    patterns, warnings = _normalize_entities(cfg.entities)
    for w in warnings:
        log.warning(w)

    server = Server()
    await server.init()
    server.set_endpoint(cfg.endpoint)
    server.set_server_name(cfg.server_name)

    log.info("OPC UA server initialized")

    idx = await server.register_namespace(cfg.namespace_uri)

    objects = server.nodes.objects
    ha_obj = await objects.add_object(idx, "HomeAssistant")

    # Create variables for each entity (resolved later after wildcard expansion)
    var_nodes: Dict[str, Any] = {}

    async def on_state(entity_id: str, state: Optional[str]) -> None:
        node = var_nodes.get(entity_id)
        if not node:
            return
        await node.write_value(coerce_variant(state))

    async with server:
        async with aiohttp.ClientSession() as session:
            # Assist: expand wildcard patterns, verify entity IDs exist in HA, and initialize values.
            try:
                states = await ha_ws_get_states(session, supervisor_ws_url(), supervisor_token())
                all_entity_ids = sorted(states.keys())

                resolved: List[str] = []
                unmatched_patterns: List[str] = []

                for pat in patterns:
                    if "*" in pat:
                        # Only support '*' wildcard; treat other glob chars literally by escaping them.
                        safe_pat = pat.replace("?", "[?]").replace("[", "[[]").replace("]", "[]]")
                        matches = [eid for eid in all_entity_ids if fnmatch.fnmatchcase(eid, safe_pat)]
                        if not matches:
                            unmatched_patterns.append(pat)
                        resolved.extend(matches)
                    else:
                        resolved.append(pat)

                # De-duplicate while preserving order
                seen: Set[str] = set()
                cfg.entities = []
                for ent in resolved:
                    if ent in seen:
                        continue
                    seen.add(ent)
                    cfg.entities.append(ent)

                if unmatched_patterns:
                    log.warning(
                        "The following wildcard patterns matched no entities: %s",
                        ", ".join(unmatched_patterns),
                    )

                missing = [e for e in cfg.entities if e not in states]
                if missing:
                    log.warning(
                        "The following configured entities were not found in Home Assistant: %s",
                        ", ".join(missing),
                    )

                # Create OPC UA variables for resolved entities
                for ent in cfg.entities:
                    browse = sanitize_browse_name(ent)
                    node = await ha_obj.add_variable(idx, browse, ua.Variant("unknown", ua.VariantType.String))
                    await node.set_writable(False)
                    var_nodes[ent] = node

                # Initialize values for entities that exist
                for ent, node in var_nodes.items():
                    st = states.get(ent)
                    if st is not None:
                        await node.write_value(coerce_variant((st.get("state") if isinstance(st, dict) else None)))
            except Exception:
                log.exception("Could not expand/validate entities via Home Assistant websocket")

            ws_url = supervisor_ws_url()
            log.info("Connecting to Home Assistant websocket: %s", ws_url)

            await ha_ws_listen(
                session=session,
                ws_url=ws_url,
                token=supervisor_token(),
                entities=cfg.entities,
                on_state=on_state,
            )


if __name__ == "__main__":
    asyncio.run(main())
