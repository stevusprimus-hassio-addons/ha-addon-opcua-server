# OPC UA Server (Home Assistant Add-on)

This add-on runs an OPC UA server and exposes selected Home Assistant entity states as OPC UA variables.

## Configuration

Example:

```yaml
endpoint: opc.tcp://0.0.0.0:4840/ha/
namespace_uri: urn:homeassistant:opcua
server_name: HomeAssistant OPC UA
entities:
  - sensor.temperature
  - sensor.humidity
  - binary_sensor.door
```

## Node layout

- `Objects/HomeAssistant/<entity_id with '.' replaced by '_'>`

Example:

- `sensor.temperature` → `Objects/HomeAssistant/sensor_temperature`

## Notes

- This initial version exposes only the entity `state` as a scalar value.
- OPC UA TLS/security policies are not enabled yet.
