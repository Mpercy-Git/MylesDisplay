# MylesDisplay dashboard card

The card uses Home Assistant's MQTT integration and the GIF support in the
MylesDisplay firmware. Upload the current `trafficlight_toggle.py`,
`gifdecoder.py`, and `mqtt_simple.py` to the panel first.

## Install from Marketplace or HACS

1. Open **Settings → Marketplace** (or **HACS** on an older Home Assistant).
2. From the menu, open **Custom repositories**, enter
   `https://github.com/Mpercy-Git/MylesDisplay`, and select **Dashboard**.
3. Open **MylesDisplay**, install it, and reload your dashboard.
4. Edit the dashboard, choose **Add card → MylesDisplay**, configure the
   optional controls, and save.

If you previously installed the card manually, remove the old
`/local/myles-display-card.js` dashboard resource so Home Assistant loads only
the Marketplace copy.

## Manual installation

1. Copy [myles-display-card.js](../dist/myles-display-card.js) into your Home Assistant configuration directory
   as `www/myles-display-card.js`. If you just created the `www` directory,
   restart Home Assistant so it serves the directory.
2. Under **Settings → Dashboards → Resources**, add
   `/local/myles-display-card.js` as a **JavaScript module**. Enable Advanced
   Mode in your user profile if Resources is hidden.
3. Edit a dashboard, select **Add card**, and choose **MylesDisplay**. Set the
   title, MQTT topic prefix, and any optional entities in the visual editor,
   then save. You can reopen the form with **Edit** on an existing card.

If you're updating a copy you already installed, replace the JavaScript file
and reload the dashboard. If the browser still shows the old card, change the
resource URL to `/local/myles-display-card.js?v=2` to refresh its cached copy.

Try the repository's [sample GIF](../nancat_small.gif). Choose it in the card,
check the preview, then select **Send GIF**. The panel loops the animation.
**Next mode** advances the panel. **Stop GIF** stops playback and clears the
loaded animation. The preview shows the selected file.

GIFs must fit within 64 × 32 pixels and be no larger than 16 KiB. The card
publishes with retain off. By default, it sends commands under
`trafficlight/command` and GIF data under `trafficlight/gif/set`.

## Optional controls

Use the entity pickers to show the mode selector, brightness slider, and
scrolling message field. Leave a picker blank to hide that control. You can
also configure the same options in YAML.

```yaml
type: custom:myles-display-card
title: MylesDisplay
topic_prefix: trafficlight
mode_entity: select.traffic_light_mode_select
brightness_entity: number.traffic_light_brightness
message_entity: text.traffic_light_message
```

Copy the actual IDs from the **Traffic Light Display** device in Home Assistant
if they differ from the example. Set `topic_prefix` only if your firmware uses a
different MQTT topic prefix.
