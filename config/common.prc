# Server settings
version v0.20.0 Alpha


server-version tt-ap-edition

# Developer settings
want-dev false
schellgames-dev false

# Logging
console-output true
notify-level-gobj error
notify-level-collide warning
notify-level-chan warning
notify-level-gobj warning
notify-level-loader warning
notify-integrate false
notify-timestamp false
default-directnotify-level info

# Window settings
load-display pandagl
window-title Toontown: Archipelago
win-origin -2 -2
depth-bits 24
frame-rate-meter-text-pattern %0.f FPS
frame-rate-meter-update-interval 0.001

# Audio settings
audio-library-name p3openal_audio

# Astron
dc-file astron/dclass/ttap.dc

# Server settings
ttoff-specific-login true

# Resources
model-path /
default-model-extension .bam

# GUI settings
direct-wtext false
on-screen-debug-font ImpressBT.ttf

# Chat settings
parent-password-set true
allow-secret-chat true
want-whitelist false
force-avatar-understandable true
force-player-understandable true

# Toon News settings
want-news-page false
want-news-tab false

# Gameplay settings
want-gardening true
want-emblems true

# Misc. settings
respect-prev-transform true
language english
vfs-case-sensitive false
inactivity-timeout 180
merge-lod-bundles false
early-event-sphere true
server-data-folder backups/
model-cache-dir
texture-anisotropic-degree 16
# Harfbuzz is good for handling non-latin text.
# However, this causes odd spacing on Cog nametags, so let's disable it.
text-use-harfbuzz #f

# ------------------------------------------------------------------------------
# Cinematic outdoor lighting + procedural sky (OutdoorLighting / ProceduralSky)
# ------------------------------------------------------------------------------
# Everything below is COMMENTED OUT on purpose: values written into a .prc file
# take priority over the in-game options menu (settings.json).  Uncomment a line
# and change its value to force that behaviour for every player/launch.
#
# Master switches:
#   want-modern-outdoor-lighting #t     # whole cinematic lighting rig
#   want-procedural-sky           #t     # shader sky dome instead of model sky
#   want-day-night-cycle          #t     # 24h keyframed lights/fog/sky
#   dynamic-shadows               #t     # sun shadows + OTP drop shadows
#   want-aurora-borealis          #t     # aurora in Brrrgh / Dreamland skies
#
# Per-feature toggles:
#   lighting-bloom-enabled        #t
#   lighting-god-rays             #t
#   lighting-fog-enabled          #t
#   lighting-specular-enabled     #f
#   lighting-tonemap-enabled      #t
#   lighting-vignette-enabled     #f
#   lighting-streetlamps-enabled  #t     # night lamp point lights
#   lighting-pointlight-shadows   #t
#   lighting-contact-shadows      #t
#   lighting-cel-shading          #f
#   lighting-experimental-world-shadows #t
#   want-water-reflections        #t
#   motion-blur                   #f
#
# Quality / tuning:
#   shadow-quality                high    # off | low | medium | high
#   sky-cloud-quality             high    # off | low | medium | high
#   lighting-intensity            1.0
#   lighting-exposure             1.0
#   lighting-color-temp           0.0     # -1 cool ... +1 warm
#   lighting-vignette-strength    0.25
#   fog-density-multiplier        1.0
#   drop-shadow-strength          0.5
#   lighting-tonemap-mode         ACES    # ACES | Filmic | Reinhard | None
#   day-night-mode                Dynamic # Dynamic | Real-Time Sync | Always Noon |
#                                        # Always Sunset | Always Midnight | Always Dawn
#                                        # (quote multi-word values: day-night-mode "Always Midnight")
#   day-duration-minutes          10.0
#   night-duration-minutes        5.0
#   day-night-speed               1.0
#   lighting-physical-atmosphere  #t
#   sky-sun-size                  2.0
#   sky-exposure                  1.08
#   sky-moon-phase                0.62
#   sky-moon-angular-radius       0.0105
#   sky-milky-way-strength        1.0
#   sky-cloud-shadow-strength     1.0
#
# Debug:
#   lighting-debug                 #f     # verbose [OutdoorLighting] logs + bisect hotkeys
#   lighting-shadow-test-scene     #f     # spawn a shadow test cube near the camera
#
# Zone/profile hooks — repoint a numeric zone or hood ID at a different lighting
# profile.  Repeat the line for each mapping; these win over the built-in
# ToontownGlobals-derived tables (styles: tt, dd, dg, mm, br, dl, gs, estate,
# playground, tt_street, ..., sellbot_hq, cashbot_hq, lawbot_hq, bossbot_hq,
# golf_course, tutorial, party, oz, ...).  Runtime equivalents live on
# OutdoorLighting as registerHoodProfile() / registerZoneProfile().
#   lighting-zone-profile-map 10100 tt_street
#   lighting-zone-profile-map 4100  br_street
#   lighting-hood-profile-map 3000  dl
#
# The knobs above are also settable at runtime by the in-game options menu when
# they are NOT written in a prc file (see toontown/settings/Settings.py and the
# LightingConfig module for the exact resolution order).
