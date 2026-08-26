from dataclasses import asdict, dataclass, fields
import json
from typing import Union, Any
from pathlib import Path

from direct.showbase.MessengerGlobal import messenger
# Valid types for a setting.
ControlSetting = dict[str, str]
Setting = Union[str, int, bool, list, float, ControlSetting]


@dataclass
class ControlSettings:
    MOVE_UP: str = "arrow_up"
    MOVE_DOWN: str = "arrow_down"
    MOVE_LEFT: str = "arrow_left"
    MOVE_RIGHT: str = "arrow_right"
    JUMP: str = "control"
    SPRINT: str = "shift"
    SCREENSHOT: str = "f9"
    TOGGLE_RUN_HOTKEY: str = "f8"
    MAP_PAGE_HOTKEY: str = "escape"
    FRIENDS_LIST_HOTKEY: str = "f7"
    STREET_MAP_HOTKEY: str = "alt"
    INVENTORY_HOTKEY: str = "home"
    QUEST_HOTKEY: str = "end"
    GALLERY_HOTKEY: str = "g"
    LOCATIONS_HOTKEY: str = "v"
    ELEVATOR_HOTKEY: str = "f"
    CRANE_GRAB_KEY: str = "control"
    ACTION_BUTTON: str = "delete"
    SECONDARY_ACTION: str = "insert"
    CHAT_HOTKEY: str = "t"


class Settings:
    # All controls with their respective default values.
    controls = ControlSettings()

    # All settings with their respective default values.
    defaultSettings = {
        "borderless": False,
        "music": True,
        "sfx": True,
        "toon-chat-sounds": True,
        'ap-sounds': True,
        "resolution": [1280, 720],
        "music-volume": 0.4,
        "sfx-volume": 0.4,
        "competitive-boss-scoring": True,
        "report-errors": True,
        "anti-aliasing": 0,
        "anisotropic-filter": 8,
        "frame-blending": True,
        "controls": asdict(controls),
        "vertical-sync": True,
        "frame-rate-meter": False,
        "fovEffects": True,
        "cam-toggle-lock": False,
        "movement_mode": "TTCC",
        "sprint_mode": "Hold",
        "magic-word-activator": 0,
        "camSensitivityX": 0.25,
        "camSensitivityY": 0.1,
        "fps-limit": 0,
        'laff-display': True,
        'battle-speed': 2,
        'new-popup': True,
        'random-music': False,
        'random-music-style': 'Mix',
        "archipelago-textsize": 0.5,
        "archipelago-log-bg": False,
        'boss-alerts': True,
        'bk-warning': True,
        # Modern outdoor rendering. Missing keys in existing settings files
        # automatically fall back to these values.
        'want-modern-outdoor-lighting': True,
        'dynamic-shadows': True,
        'shadow-quality': 'high',
        'lighting-bloom-enabled': True,
        'lighting-god-rays': True,
        'lighting-fog-enabled': True,
        'lighting-specular-enabled': False,
        'lighting-tonemap-enabled': True,
        'lighting-vignette-enabled': False,
        'lighting-vignette-strength': 0.25,
        'lighting-intensity': 1.0,
        'lighting-color-temp': 0.0,
        'want-procedural-sky': True,
        'sky-cloud-quality': 'high',
        'sky-cloud-shadow-strength': 1.0,
        'sky-milky-way-strength': 1.0,
        'sky-moon-phase': 0.62,
        'sky-moon-angular-radius': 0.0105,
        'sky-exposure': 1.08,
        'want-day-night-cycle': True,
        'day-night-speed': 1.0,
        'day-duration-minutes': 10.0,
        'night-duration-minutes': 5.0,
        'day-night-mode': 'Dynamic',
        'want-aurora-borealis': True,
        'lighting-contact-shadows': True,
        'lighting-streetlamps-enabled': True,
        'lighting-pointlight-shadows': True,
        # Experimental real-time world shadow maps (directional shadows cast by
        # buildings/trees onto the ground).  Kept opt-in because the generated-
        # shader path can still exhibit camera-dependent artifacts on some
        # flattened legacy DNA / driver combinations.
        'lighting-experimental-world-shadows': True,
        'lighting-shadow-softness': 1.0,
        'lighting-tonemap-mode': 'ACES',
        'lighting-exposure': 1.0,
        'want-water-reflections': True,
        'lighting-cel-shading': False,
        'motion-blur': False,
        'motion-blur-strength': 1.0,
        'indoor-dynamic-shadows': True,
        'fog-density-multiplier': 1.0,
        'drop-shadow-strength': 0.5,
        'force-shader-support': False,
        'shadow-bypass-shader-check': False,
        'lighting-debug': False,
        # Options below this comment will not be exposed by OptionsPage
        # They can still be configurable by the end user
        "bk-warning-seen-runs": [],
        "want-legacy-models": False,
        "experimental-multithreading": False,
        'discord-rich-presence': False,
        "color-blind-mode": False,
    }
    settingsFile = Path.home() / "Documents" / "Toontown Archipelago" / "settings.json"


    def __init__(self) -> None:
        try:
            with self.settingsFile.open(encoding='utf-8') as f:
                self._settings = json.load(f)
        except FileNotFoundError:
            self.settingsFile.parent.mkdir(parents=True, exist_ok=True)
            self._settings = self.defaultSettings.copy()
        except Exception as e:
            raise e

        # Re-instantiate the ControlSettings with our saved controls.
        self.updateControls(self.get("controls"))
        self.write()

    def get(self, setting: str) -> Setting:
        return self._settings.get(setting, self.defaultSettings.get(setting))

    def set(self, setting: str, value: Setting) -> None:
        if not isinstance(value, type(self.defaultSettings.get(setting))):
            return
        self._settings[setting] = value

    def setControl(self, control: str, keybind: str) -> None:
        # First, get the control setting.
        controls: ControlSetting = self.get("controls")
        # Replace the keybind at the control.
        controls[control] = keybind
        # Update the control settings with our new controls.
        self.updateControls(controls)

    def getControl(self, control: str) -> str:
        return self.getControls().get(control, "")

    def getControls(self) -> dict[str, Any]:
        return asdict(self.controls)

    def updateControls(self, controls: dict[str, str]) -> None:
        # Ensure that extra controls in our list dont crash (typically for going back to a previous version)
        valid_args = {f.name: controls[f.name] for f in fields(ControlSettings) if f.name in controls}
        self.controls = ControlSettings(**valid_args)
        self.set("controls", asdict(self.controls))

    def write(self) -> None:
        with self.settingsFile.open("w", encoding='utf-8') as _settings:
            # Clean the settings dictionary before saving it to the file.
            self.clean()
            json.dump(self._settings, _settings, sort_keys=True, indent=4)

    def clean(self) -> None:
        """
        Removes all keys in settings which shouldn't exist.
        (they don't exist in defaultSettings)
        """
        for setting in list(self._settings):
            if setting not in self.defaultSettings:
                del self._settings[setting]
