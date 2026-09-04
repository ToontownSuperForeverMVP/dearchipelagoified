"""OptionsPage module: contains the OptionsPage class"""
import os
from enum import IntEnum, auto
from typing import Optional

from panda3d.core import TextNode, Vec4
from direct.directnotify import DirectNotifyGlobal
from direct.fsm.FSM import FSM
from direct.gui.DirectGui import *
from direct.showbase.MessengerGlobal import messenger

from otp.otpbase.OTPLocalizerEnglish import SpeedChatStaticTextToontown
from otp.speedchat.SpeedChatGlobals import speedChatStyles
from toontown.settings.Settings import Setting
from toontown.settings import ChatTheme
from toontown.settings.ChatTheme import normalize_hex, parse_hex_color
from toontown.shtiker.ShtikerPage import ShtikerPage
from toontown.toonbase import TTLocalizer
from toontown.toontowngui import TTDialog
from toontown.toontowngui.ToontownScrolledFrame import ToontownScrolledFrame

from direct.distributed.ClockDelta import *


class OptionTypes(IntEnum):
    BUTTON = auto()
    DROPDOWN = auto()
    SLIDER = auto()
    CONTROL = auto()
    BUTTON_SPEEDCHAT = auto()
    COLOR = auto()
    BUTTON_ACTION = auto()


OptionToType = {
    # Gameplay
    'camSensitivityX': OptionTypes.SLIDER,
    'camSensitivityY': OptionTypes.SLIDER,
    'movement_mode': OptionTypes.BUTTON,
    'sprint_mode': OptionTypes.BUTTON,
    'fovEffects': OptionTypes.BUTTON,
    'cam-toggle-lock': OptionTypes.BUTTON,
    'boss-alerts': OptionTypes.BUTTON,
    'bk-warning': OptionTypes.BUTTON,
    'speedchat-style': OptionTypes.BUTTON_SPEEDCHAT,
    'discord-rich-presence': OptionTypes.BUTTON,
    'archipelago-textsize': OptionTypes.SLIDER,
    'archipelago-log-bg': OptionTypes.BUTTON,
    'archipelago-chat-opacity': OptionTypes.SLIDER,
    'archipelago-chat-width': OptionTypes.SLIDER,
    'archipelago-chat-height': OptionTypes.SLIDER,
    'archipelago-chat-theme': OptionTypes.DROPDOWN,
    'archipelago-chat-timestamps': OptionTypes.BUTTON,
    'archipelago-chat-max-history': OptionTypes.DROPDOWN,
    'archipelago-chat-auto-show': OptionTypes.BUTTON,
    'archipelago-chat-animations': OptionTypes.BUTTON,
    'archipelago-chat-snap': OptionTypes.BUTTON,
    'archipelago-chat-mentions': OptionTypes.BUTTON,
    'archipelago-chat-position-lock': OptionTypes.BUTTON,
    'archipelago-chat-font': OptionTypes.DROPDOWN,
    'archipelago-chat-text-color': OptionTypes.COLOR,
    'archipelago-chat-bg-color': OptionTypes.COLOR,
    'archipelago-chat-accent-color': OptionTypes.COLOR,
    'archipelago-chat-header-color': OptionTypes.COLOR,
    'archipelago-chat-title-color': OptionTypes.COLOR,
    'archipelago-chat-channel-ap': OptionTypes.COLOR,
    'archipelago-chat-channel-toon': OptionTypes.COLOR,
    'archipelago-chat-channel-you': OptionTypes.COLOR,
    'archipelago-chat-channel-client': OptionTypes.COLOR,
    'archipelago-chat-channel-error': OptionTypes.COLOR,
    'ap-reward-font': OptionTypes.DROPDOWN,
    'ap-reward-text-color': OptionTypes.COLOR,
    'ap-reward-bg-color': OptionTypes.COLOR,
    'ap-reward-text-scale': OptionTypes.SLIDER,
    'color-blind-mode': OptionTypes.BUTTON,
    'want-legacy-models': OptionTypes.BUTTON,
    'laff-display': OptionTypes.BUTTON,
    'new-popup': OptionTypes.BUTTON,
    'battle-speed': OptionTypes.DROPDOWN,
    'arrange-ui': OptionTypes.BUTTON_ACTION,

    # Privacy
    "competitive-boss-scoring": OptionTypes.BUTTON,
    "report-errors": OptionTypes.BUTTON,

    # Video
    "borderless": OptionTypes.BUTTON,
    "resolution": OptionTypes.DROPDOWN,
    "vertical-sync": OptionTypes.BUTTON,
    "anisotropic-filter": OptionTypes.DROPDOWN,
    "anti-aliasing": OptionTypes.DROPDOWN,
    "frame-rate-meter": OptionTypes.BUTTON,
    "fps-limit": OptionTypes.DROPDOWN,
    "dynamic-shadows": OptionTypes.BUTTON,
    "shadow-quality": OptionTypes.DROPDOWN,
    "lighting-experimental-world-shadows": OptionTypes.BUTTON,
    "drop-shadow-strength": OptionTypes.SLIDER,
    "want-procedural-sky": OptionTypes.BUTTON,
    "sky-cloud-quality": OptionTypes.DROPDOWN,
    "lighting-bloom-enabled": OptionTypes.BUTTON,
    "lighting-god-rays": OptionTypes.BUTTON,
    "lighting-fog-enabled": OptionTypes.BUTTON,
    "fog-density-multiplier": OptionTypes.SLIDER,
    "lighting-intensity": OptionTypes.SLIDER,
    "lighting-exposure": OptionTypes.SLIDER,
    "want-day-night-cycle": OptionTypes.BUTTON,
    "day-night-mode": OptionTypes.DROPDOWN,
    "day-duration-minutes": OptionTypes.SLIDER,
    "night-duration-minutes": OptionTypes.SLIDER,
    "want-aurora-borealis": OptionTypes.BUTTON,
    "lighting-contact-shadows": OptionTypes.BUTTON,
    "lighting-streetlamps-enabled": OptionTypes.BUTTON,
    "lighting-tonemap-mode": OptionTypes.DROPDOWN,
    "want-water-reflections": OptionTypes.BUTTON,
    "motion-blur": OptionTypes.BUTTON,
    "motion-blur-strength": OptionTypes.SLIDER,

    # Audio
    "music": OptionTypes.BUTTON,
    "sfx": OptionTypes.BUTTON,
    "music-volume": OptionTypes.SLIDER,
    "sfx-volume": OptionTypes.SLIDER,
    "toon-chat-sounds": OptionTypes.BUTTON,
    'ap-sounds': OptionTypes.BUTTON,
    "random-music": OptionTypes.BUTTON,
    "random-music-style": OptionTypes.BUTTON,
    "refresh-audio": OptionTypes.BUTTON
}

# All control options are naturally going to be of the CONTROL option type,
# so let's fill the dictionary as such.
controls = list(base.settings.getControls())
OptionToType.update(dict(zip(controls, [OptionTypes.CONTROL for _ in range(len(controls))])))


class OptionsPage(ShtikerPage):
    """OptionsPage class"""

    notify = DirectNotifyGlobal.directNotify.newCategory("OptionsPage")

    # special methods
    def __init__(self):
        """__init__(self)
        OptionsPage constructor: create the options page
        """
        super().__init__()

        if __debug__:
            base.op = self

    def load(self):
        assert self.notify.debugStateCall(self)
        super().load()

        # Create the OptionsTabPage
        self.optionsTabPage = OptionsTabPage(self)
        self.optionsTabPage.hide()

        titleHeight = 0.61  # bigger number means higher the title
        self.title = DirectLabel(
            parent=self,
            relief=None,
            text=TTLocalizer.OptionsPageTitle,
            text_scale=0.12,
            pos=(0, 0, titleHeight),
        )

    def enter(self):
        assert self.notify.debugStateCall(self)

        messenger.send('wakeup')

        self.optionsTabPage.enter()

        # Make the call to the superclass enter method.
        super().enter()

    def exit(self):
        assert self.notify.debugStateCall(self)
        base.localAvatar.sendUpdate("requestSetBattleSpeed", [base.settings.get('battle-speed')])
        self.optionsTabPage.exit()

        # Make the call to the superclass exit method.
        super().exit()

    def unload(self):
        assert self.notify.debugStateCall(self)
        self.optionsTabPage.unload()

        # Cleanup the direct label.
        self.title.destroy()
        del self.title

        # Make the call to the superclass unload method.
        super().unload()


class OptionsTabPage(DirectFrame, FSM):
    tabOptions = {
        "Gameplay": [
            'camSensitivityX',
            'camSensitivityY',
            'movement_mode',
            'sprint_mode',
            'battle-speed',
            'new-popup',
            'fovEffects',
            'cam-toggle-lock',
            'boss-alerts',
            'bk-warning',
            'speedchat-style',
            'discord-rich-presence',
            'color-blind-mode',
            'want-legacy-models',
            'laff-display',
            'arrange-ui',
        ],
        "Privacy": [
            "competitive-boss-scoring",
            "report-errors"
        ],
        "Controls": [*list(base.settings.getControls())],
        "Video": [
            "borderless", "resolution", "vertical-sync", "anisotropic-filter",
            "anti-aliasing", "frame-rate-meter", "fps-limit",
            "dynamic-shadows", "shadow-quality", "drop-shadow-strength",
            "lighting-experimental-world-shadows",
            "lighting-contact-shadows", "lighting-streetlamps-enabled",
            "want-procedural-sky", "sky-cloud-quality", "want-aurora-borealis",
            "lighting-bloom-enabled", "lighting-god-rays",
            "lighting-fog-enabled", "fog-density-multiplier",
            "lighting-intensity", "lighting-exposure", "lighting-tonemap-mode",
            "want-day-night-cycle", "day-night-mode",
            "day-duration-minutes", "night-duration-minutes",
            "want-water-reflections",
            "motion-blur",
            "motion-blur-strength",
        ],
        "Audio": [
            "music", "sfx", "music-volume", "sfx-volume", "toon-chat-sounds",
            'ap-sounds', "random-music", "random-music-style", "refresh-audio"
        ],
        "Chat": [
            'archipelago-textsize',
            'archipelago-log-bg',
            'archipelago-chat-font',
            'archipelago-chat-text-color',
            'archipelago-chat-bg-color',
            'archipelago-chat-accent-color',
            'archipelago-chat-header-color',
            'archipelago-chat-title-color',
            'archipelago-chat-channel-ap',
            'archipelago-chat-channel-toon',
            'archipelago-chat-channel-you',
            'archipelago-chat-channel-client',
            'archipelago-chat-channel-error',
            'archipelago-chat-opacity',
            'archipelago-chat-width',
            'archipelago-chat-height',
            'archipelago-chat-theme',
            'archipelago-chat-timestamps',
            'archipelago-chat-max-history',
            'archipelago-chat-auto-show',
            'archipelago-chat-animations',
            'archipelago-chat-snap',
            'archipelago-chat-mentions',
            'archipelago-chat-position-lock',
        ],
        "Items": [
            'ap-reward-font',
            'ap-reward-text-scale',
            'ap-reward-text-color',
            'ap-reward-bg-color',
        ],
    }

    def __init__(self, parent=aspect2d, **kw):
        DirectFrame.__init__(self, parent, **kw)
        FSM.__init__(self, "OptionsTabPageNEW")

        self._parent = parent

        self.tabs: dict[str, DirectButton] = {}
        self.options: dict[str, OptionsScrolledFrame] = {}

        self.load()

    def load(self) -> None:
        # Load the Fish Page to borrow its tabs
        base.loader.loadModel("phase_3.5/models/gui/fishingBook", callback=self.loadTabs)
        # Load the "Exit Toontown" button
        base.loader.loadModel("phase_3/models/gui/quit_button", callback=self.createExitButton)
        # Load (& hide) the options frames
        base.loader.loadModel("phase_3/models/gui/quit_button", callback=self.createTabs)

    def loadTabs(self, gui):
        # The blue and yellow colors are trying to match the
        # rollover and select colors on the options page:
        normalColor = (1, 1, 1, 1)
        clickColor = (.8, .8, 0, 1)
        rolloverColor = (0.15, 0.82, 1.0, 1)
        disabledColor = (1.0, 0.98, 0.15, 1)

        # The tab model's label is offset +0.10 from its node.  Center the
        # visible labels (not just their nodes) across the same 1.56-wide span
        # used by the original five tabs.  This keeps the added Chat tab
        # perfectly even with Gameplay through Audio instead of leaving the
        # whole row skewed to the left.
        tabCount = len(self.tabOptions)
        visibleSpan = 1.56
        labelOffset = 0.10
        interval = visibleSpan / max(1, tabCount - 1)
        initial = -labelOffset - visibleSpan / 2

        for i, tab in enumerate(list(self.tabOptions)):
            x = initial + i * interval
            self.tabs[tab] = DirectButton(
                parent=self, relief=None, text=TTLocalizer.OptionsPageTabs[i],
                text_scale=0.06, text_align=TextNode.ACenter,
                text_pos=(0.1, 0.0, 0.0),
                image=gui.find("**/tabs/polySurface1"),
                image_pos=(0.525, 1, -0.91), image_hpr=(0, 0, -90),
                image_scale=(0.033, 0.033, 0.035),
                image_color=normalColor, image1_color=clickColor,
                image2_color=rolloverColor, image3_color=disabledColor,
                text_fg=Vec4(0.2, 0.1, 0, 1), command=self.request,
                extraArgs=[tab], pos=(x, 0, 0.77)
            )

        gui.remove_node()

    def createExitButton(self, gui) -> None:
        self.exitButton = DirectButton(
            parent=self, relief=None,
            image=(gui.find("**/QuitBtn_UP"),
                   gui.find("**/QuitBtn_DN"),
                   gui.find("**/QuitBtn_RLVR"),
                   ),
            image_scale=1.15,
            text=TTLocalizer.OptionsPageExitToontown,
            text_scale=0.052,
            text_pos=(0, -0.02),
            textMayChange=False,
            pos=(0.45, 0, -0.6),
            command=self.__handleExitShowWithConfirm,
        )

        gui.remove_node()

    def createTabs(self, gui) -> None:
        for tab, options in self.tabOptions.items():
            frame = OptionsScrolledFrame(parent=self._parent, options=options, gui=gui)
            frame.hide()
            self.options[tab] = frame

        gui.remove_node()

    def unload(self) -> None:
        for tab in self.tabs.values():
            tab.destroy()

        self.tabs = {}

        self.destroyOptions()

        self.exitButton.destroy()
        self.exitButton = None

    def enter(self) -> None:
        self.show()

        base.localAvatar.disableOldPieKeys()
        self.request("Gameplay")

    def exit(self) -> None:
        self.hide()
        self.request("Off")

        # Write the settings to the local JSON file.
        base.settings.write()
        base.localAvatar.resetPieKeys()
        base.localAvatar.updateOverhead()

    def updateTabs(self) -> None:
        messenger.send("wakeup")

        for tab in self.tabs.values():
            tab["state"] = DGG.NORMAL

        currState = self.getCurrentOrNextState()
        if currState in self.tabs:
            self.tabs[currState]["state"] = DGG.DISABLED

    def destroyOptions(self) -> None:
        for frame in self.options.values():
            frame.destroy()

        self.options = {}

    """
    FSM states
    """

    def enterGameplay(self) -> None:
        self.updateTabs()
        self.options["Gameplay"].show()

    def exitGameplay(self) -> None:
        self.options["Gameplay"].hide()

    def enterPrivacy(self) -> None:
        self.updateTabs()
        self.options["Privacy"].show()

    def exitPrivacy(self) -> None:
        self.options["Privacy"].hide()

    def enterControls(self) -> None:
        self.updateTabs()
        self.options["Controls"].show()

    def exitControls(self) -> None:
        self.options["Controls"].hide()

    def enterVideo(self) -> None:
        self.updateTabs()
        self.options["Video"].show()

    def exitVideo(self) -> None:
        self.options["Video"].hide()

    def enterAudio(self) -> None:
        self.updateTabs()
        self.options["Audio"].show()

    def exitAudio(self) -> None:
        self.options["Audio"].hide()

    def enterChat(self) -> None:
        self.updateTabs()
        self.options["Chat"].show()

    def exitChat(self) -> None:
        self.options["Chat"].hide()

    def enterItems(self) -> None:
        self.updateTabs()
        self.options["Items"].show()

    def exitItems(self) -> None:
        self.options["Items"].hide()

    """
    Exit button
    """

    def __handleExitShowWithConfirm(self):
        # For exiting from the options panel to the avatar chooser.
        """__handleExitShowWithConfirm(self)
        """
        self.confirm = TTDialog.TTGlobalDialog(
            doneEvent="confirmDone",
            message=TTLocalizer.OptionsPageExitConfirm,
            style=TTDialog.TwoChoice)
        self.confirm.show()
        self._parent.doneStatus = {
            "mode": "exit",
            "exitTo": "closeShard"}
        self.accept("confirmDone", self.__handleConfirm)

    def __handleConfirm(self):
        """__handleConfirm(self)
        """
        status = self.confirm.doneStatus
        self.ignore("confirmDone")
        self.confirm.cleanup()
        del self.confirm
        if (status == "ok"):
            base.cr._userLoggingOut = True
            messenger.send(self._parent.doneEvent)
            # self.cr.loginFSM.request("chooseAvatar", [self.cr.avList])


class OptionsScrolledFrame(ToontownScrolledFrame):
    width = 0.8
    height = 0.53

    def __init__(self, parent=None, options: list[str] = None, gui=None, **kw) -> None:
        super().__init__(
            parent, relief=None,
            pos=(0, 0, 0),
            canvasSize=(-self.width, self.width, -self.height, self.height),
            frameSize=(-self.width, self.width, -self.height, self.height),
            **kw
        )
        self.initialiseoptions(OptionsScrolledFrame)

        self.optionNames = options or []

        self.optionElements = []
        for index, option in enumerate(self.optionNames):
            element = OptionElement(parent, parent=self.getCanvas(), name=option, index=index, gui=gui)
            self.bindToScroll(element)
            self.optionElements.append(element)

        optionAmt = len(self.optionElements)

        # Update the scrollbar if there are more than x elements.
        canvasHeight = ((optionAmt * 0.1) - self.height) if optionAmt > 10 else self.height

        self["canvasSize"] = (-self.width, self.width, -canvasHeight, self.height)
        self.setCanvasSize()

    def destroy(self) -> None:
        if hasattr(self, "optionElements"):
            for option in self.optionElements:
                option.destroy()
            del self.optionElements

        super().destroy()


class DropdownScrolledFrame(ToontownScrolledFrame):
    width = 0.35
    height = 0.3
    offset = 0.225

    def __init__(self, optionName: str, parent=None, pos=(0, 0, 0), options: list[str] = None, command=None, cancelCommand=None, **kw
                 ) -> None:
        targetX = pos[0]
        targetZ = pos[2] - self.offset
        if targetZ - self.height < -0.55:
            targetZ = pos[2] + self.offset
        if targetZ - self.height < -0.55:
            targetZ = -0.55 + self.height
        if targetZ + self.height > 0.55:
            targetZ = 0.55 - self.height

        if parent is None:
            parent = aspect2d

        super().__init__(
            parent, relief=None,
            pos=(targetX, pos[1], targetZ),
            canvasSize=(-self.width, self.width, -self.height, self.height),
            frameSize=(-self.width, self.width, -self.height, self.height),
            **kw
        )
        self.initialiseoptions(DropdownScrolledFrame)
        self.reparentTo(parent, DGG.NO_FADE_SORT_INDEX)

        self.optionName = optionName
        self.optionNames = options or []
        self.cancelCommand = cancelCommand

        gui = base.loader.loadModel("phase_3/models/gui/quit_button")

        self.cancelButton = DirectButton(
            parent=parent,
            relief=None,
            frameSize=(-2.0, 2.0, -2.0, 2.0),
            command=self._cancel,
        )
        self.cancelButton.reparentTo(parent, DGG.FADE_SORT_INDEX + 10)
        self.cancelButton.setBin('gui-popup', 4990)

        self.optionElements = []
        for index, option in enumerate(self.optionNames):
            element = DirectButton(
                parent=self.getCanvas(), relief=None, pos=(0, 0, self.offset - (index * 0.1)),
                text=self.formatSetting(option),
                text_scale=0.048, image_pos=(0, 0, 0.02),
                image=(
                    gui.find("**/QuitBtn_UP"),
                    gui.find("**/QuitBtn_DN"),
                    gui.find("**/QuitBtn_RLVR"),
                ),
                image_scale=(0.75, 1, 1),
                command=command, extraArgs=[option]
            )
            self.bindToScroll(element)
            self.optionElements.append(element)

        gui.remove_node()

        optionAmt = len(self.optionElements)

        canvasHeight = ((optionAmt * 0.1) - self.height) if optionAmt > 4 else self.height

        self["canvasSize"] = (-self.width, self.width, -canvasHeight, self.height)
        self.setCanvasSize()

        base.transitions.fadeScreen(0.5)

    def _cancel(self) -> None:
        if self.cancelCommand:
            self.cancelCommand()
        else:
            self.destroy()

    def formatSetting(self, setting: Setting) -> str:
        """Given the type of setting we're dealing with, handle
        how the text on the button will display.
        """
        if isinstance(setting, list):
            # When dealing with list options, strip the brackets and
            # join the parts together.
            if self.optionName == "resolution":
                string = "x"
            else:
                string = ""

            return string.join([str(e) for e in setting]).replace("[", "").replace("[", "")
        elif isinstance(setting, int):
            # We're most likely dealing with a list of integer settings,
            # so return the string from the localizer given the current setting.
            if self.optionName == "anti-aliasing":
                return TTLocalizer.OptionAntiAlias[setting]
            if self.optionName == "anisotropic-filter":
                return TTLocalizer.OptionAnisotropic[setting]
            if self.optionName == "fps-limit":
                return TTLocalizer.OptionFPSLimit[setting]
            if self.optionName == "battle-speed":
                return TTLocalizer.OptionBattleSpeed[setting]

        return str(setting)

    def destroy(self) -> None:
        if hasattr(self, "cancelButton") and self.cancelButton:
            self.cancelButton.destroy()
            self.cancelButton = None

        if hasattr(self, "optionElements"):
            for option in self.optionElements:
                option.destroy()
            del self.optionElements

        base.transitions.noFade()

        super().destroy()


class ColorPickerFrame(DirectFrame):
    """A small popup for choosing any colour: preset swatches plus a hex box.

    Presets offer one-click choices; the text field accepts any ``#RRGGBB``
    value (passed through ``normalize_hex``) so the player really can use any
    colour they like.
    """

    SWATCH = 0.054
    COLS = 5

    def __init__(self, optionName, presets, onApply, cancelCommand=None,
                 autoEnabled=False, pos=None):
        DirectFrame.__init__(
            self, parent=aspect2d, relief=DGG.FLAT,
            frameSize=(-0.34, 0.34, -0.24, 0.30),
            frameColor=(0.05, 0.06, 0.10, 0.98),
            pos=pos or (0, 0, 0),
        )
        self.optionName = optionName
        self.onApply = onApply
        self.cancelCommand = cancelCommand
        self.autoEnabled = autoEnabled

        # Clicking anywhere outside closes the picker.
        self.cancelButton = DirectButton(
            parent=aspect2d, relief=None,
            frameSize=(-2.0, 2.0, -2.0, 2.0), command=self._cancel,
        )
        self.cancelButton.reparentTo(aspect2d, DGG.FADE_SORT_INDEX + 10)
        self.cancelButton.setBin('gui-popup', 4990)

        # For options that support it, a shortcut back to 'follow the accent'.
        if autoEnabled:
            autoActive = ChatTheme.is_auto_color(base.settings.get(optionName))
            DirectButton(
                parent=self, relief=DGG.FLAT,
                frameColor=(0.20, 0.20, 0.27, 1) if autoActive else (0.14, 0.14, 0.18, 1),
                frameSize=(-0.11, 0.11, -0.036, 0.036),
                pos=(0.25, 0, 0.245),
                text="Auto (Accent)", text_scale=0.040,
                command=self._pickAuto, text_fg=(0.75, 0.85, 1, 1) if autoActive else (0.55, 0.6, 0.7, 1),
            )

        DirectLabel(
            parent=self, relief=None, pos=(0, 0, 0.245),
            text=TTLocalizer.OptionNames.get(optionName, optionName),
            text_scale=0.052,
        )

        # One clickable swatch per preset, laid out in a grid.
        self._swatchButtons = []
        for index, color in enumerate(presets):
            row = index // self.COLS
            column = index % self.COLS
            x = -0.24 + column * (self.SWATCH + 0.012) + self.SWATCH / 2
            z = 0.17 - row * (self.SWATCH + 0.014)
            button = DirectButton(
                parent=self, relief=DGG.FLAT,
                frameSize=(-self.SWATCH / 2, self.SWATCH / 2,
                           -self.SWATCH / 2, self.SWATCH / 2),
                frameColor=(1, 1, 1, 1),
                pos=(x, 0, z),
            )
            rgb = parse_hex_color(color, Vec4(1, 1, 1, 1))
            button.setColorScale(rgb[0], rgb[1], rgb[2], 1)
            button.bind(DGG.B1CLICK, self._pick, extraArgs=[color])
            self._swatchButtons.append(button)

        lastRowZ = 0.17 - ((len(presets) - 1) // self.COLS) * (self.SWATCH + 0.014)

        DirectLabel(
            parent=self, relief=None, pos=(-0.30, 0, lastRowZ - 0.09),
            text="Hex:", text_align=TextNode.ARight, text_scale=0.045,
        )
        current = base.settings.get(optionName)
        if isinstance(current, str) and not ChatTheme.is_auto_color(current):
            defaultHex = current
        else:
            defaultHex = presets[0] if presets else ''
        self.hexEntry = DirectEntry(
            parent=self, relief=DGG.SUNKEN,
            frameColor=(0.1, 0.1, 0.15, 1),
            frameSize=(-0.12, 0.14, -0.033, 0.033),
            pos=(-0.16, 0, lastRowZ - 0.09),
            text=normalize_hex(defaultHex, '#FFFFFF'),
            text_scale=0.042,
            numLines=1, width=12, focus=0, backgroundFocus=1,
        )
        self.hexEntry.bind(DGG.TYPE, self._onHexTyped)

        self.stateLabel = DirectLabel(
            parent=self, relief=None, pos=(0.18, 0, lastRowZ - 0.09),
            text="", text_scale=0.038,
            text_fg=(0.5, 0.95, 0.55, 1),
        )

        DirectButton(
            parent=self, relief=DGG.FLAT,
            frameColor=(0.12, 0.5, 0.85, 1),
            frameSize=(-0.10, 0.10, -0.034, 0.034),
            pos=(-0.12, 0, -0.205),
            text="Apply", text_scale=0.044,
            command=self._apply, text_fg=(1, 1, 1, 1),
        )
        DirectButton(
            parent=self, relief=DGG.FLAT,
            frameColor=(0.28, 0.28, 0.34, 1),
            frameSize=(-0.10, 0.10, -0.034, 0.034),
            pos=(0.12, 0, -0.205),
            text="Cancel", text_scale=0.044,
            command=self._cancel, text_fg=(1, 1, 1, 1),
        )

        self.initialiseoptions(ColorPickerFrame)

    def _pick(self, color, _event=None):
        self.onApply(color)

    def _pickAuto(self, *_):
        self.onApply(ChatTheme.AUTO_COLOR)

    def _onHexTyped(self, *_):
        value = normalize_hex(self.hexEntry.get())
        if value.startswith("#"):
            color = parse_hex_color(value)
            self.hexEntry["frameColor"] = (color[0] * 0.35, color[1] * 0.35, color[2] * 0.35, 1)
            self.stateLabel["text"] = value
        else:
            self.stateLabel["text"] = ""

    def _apply(self, *_):
        self.onApply(self.hexEntry.get())

    def _cancel(self, *_):
        if self.cancelCommand:
            self.cancelCommand()
        else:
            self.destroy()

    def destroy(self):
        if hasattr(self, 'cancelButton') and self.cancelButton is not None:
            self.cancelButton.destroy()
            self.cancelButton = None
        for button in getattr(self, '_swatchButtons', []):
            button.destroy()
        super().destroy()


class OptionElement(DirectFrame):
    """
    Option types:
    Button: Clicking on the button will scroll through the options
    - i.e (true, false), (red, yellow, orange, blue, green), etc.
    Slider: Slide between two extremes
    - i.e (0% vol, 100% vol), (50 fov, 150 fov), etc.

    See toontown.settings.Settings.py for the default settings.
    """

    optionOptions = {}
    for option, default in base.settings.defaultSettings.items():
        if isinstance(default, bool):
            optionOptions[option] = [True, False]
        elif option == "movement_mode":
            optionOptions[option] = ["TTCC", "TTR"]
        elif option == "sprint_mode":
            optionOptions[option] = ["Hold", "Toggle"]
        elif option == "random-music-style":
            optionOptions[option] = ["Custom Only", "Mix", "In-Game Only"]

    optionOptions.update({
        "resolution": base.possibleScreenSizes,
        "anisotropic-filter": list(TTLocalizer.OptionAnisotropic),
        "anti-aliasing": list(TTLocalizer.OptionAntiAlias),
        "fps-limit": list(TTLocalizer.OptionFPSLimit),
        "battle-speed": list(TTLocalizer.OptionBattleSpeed),
        "shadow-quality": ["off", "low", "medium", "high"],
        "sky-cloud-quality": ["off", "low", "medium", "high"],
        "day-night-mode": list(TTLocalizer.OptionDayNightMode),
        "lighting-tonemap-mode": list(TTLocalizer.OptionTonemapMode),
        "archipelago-chat-theme": ["Blue", "Green", "Purple", "Orange"],
        "archipelago-chat-max-history": [50, 100, 250, 500],
    })

    sliderRanges = {
        "drop-shadow-strength": (0.15, 0.75),
        "fog-density-multiplier": (0.25, 1.50),
        "lighting-intensity": (0.50, 1.50),
        "lighting-exposure": (0.50, 2.00),
        "day-duration-minutes": (1.0, 30.0),
        "night-duration-minutes": (1.0, 20.0),
        "motion-blur-strength": (0.10, 2.00),
        "archipelago-chat-opacity": (0.15, 1.00),
        "archipelago-chat-width": (0.74, 1.40),
        "archipelago-chat-height": (0.48, 0.96),
        "ap-reward-text-scale": (0.40, 2.50),
    }

    def __init__(self, page, parent, name: str, index: int, gui, **kw):
        super().__init__(parent, **kw)

        self.page = page

        # The name of the setting.
        self.optionName = name
        self.optionType = OptionToType[self.optionName]

        if self.optionType == OptionTypes.CONTROL:
            currSetting = self.formatKeybind(base.settings.getControl(name))
        elif self.optionType == OptionTypes.BUTTON_SPEEDCHAT:
            currSetting = self.formatSpeedchat(base.localAvatar.getSpeedChatStyleIndex())
        else:
            currSetting = base.settings.get(name)

        z = 0.45 - (index * 0.1)

        # Make the label which will appear on the left-hand side of
        # the page.
        self.label = DirectLabel(
            parent=self, relief=None, pos=(-0.4, 0, z),
            text=TTLocalizer.OptionNames[self.optionName],
            text_scale=0.052,
        )

        # A separate frame for dropdown options which contains a list of option buttons.
        self.dropdownFrame: Optional[DropdownScrolledFrame] = None

        # A colour swatch box shown to the left of the hex text on colour
        # buttons, so the chosen colour is visible at a glance.
        self.colorSwatch = None

        # Make the button which will appear on the right-hand side of
        # the page.
        if self.optionType in (OptionTypes.BUTTON, OptionTypes.CONTROL, OptionTypes.BUTTON_SPEEDCHAT,
                               OptionTypes.COLOR, OptionTypes.DROPDOWN, OptionTypes.BUTTON_ACTION):
            self.optionModifier = DirectButton(
                parent=self, relief=None, pos=(0.37, 0, z),
                text=self.formatSetting(currSetting),
                text_scale=0.048, image_pos=(0, 0, 0.02),
                image=(
                    gui.find("**/QuitBtn_UP"),
                    gui.find("**/QuitBtn_DN"),
                    gui.find("**/QuitBtn_RLVR"),
                ),
                image_scale=(0.7, 1, 1),
            )
            if self.optionType == OptionTypes.COLOR:
                self.optionModifier["command"] = self._openColorPicker
                self._refreshColorSwatch()
            elif self.optionType == OptionTypes.DROPDOWN:
                self.optionModifier["command"] = self._openDropdown
            elif self.optionType == OptionTypes.BUTTON_ACTION:
                self.optionModifier["command"] = self._handleAction
            else:
                self.optionModifier["command"] = self._updateButtonOption

            if self.optionType == OptionTypes.CONTROL:
                self.checkForDuplicates()
                self.accept("controls_findDuplicates", self.checkForDuplicates)
            
            if self.optionName == 'refresh-audio':
                self.optionModifier["text"] = TTLocalizer.OptionRefresh  # This is a special case where there is no setting to display.
            elif self.optionName == 'arrange-ui':
                self.optionModifier["text"] = TTLocalizer.OptionArrangeUI  # Action button: no setting value to display.
        
        # Make the slider which will appear on the right-hand side of
        # the page.
        elif self.optionType == OptionTypes.SLIDER:
            self.optionModifier = DirectSlider(
                parent=self, relief=DGG.SUNKEN, pos=(0.37, 0, z), thumb_relief=None,
                thumb_image_scale=(0.3, 0.8, 0.8),
                frameSize=(-0.25, 0.25, -0.1, 0.1),
                image_pos=(0, 0, 0.02),
                thumb_image=(
                    gui.find("**/QuitBtn_UP"),
                    gui.find("**/QuitBtn_DN"),
                    gui.find("**/QuitBtn_RLVR"),
                ),
                value=currSetting,
                range=self.sliderRanges.get(self.optionName, (0.0, 1.0)),
                command=self._updateSliderOption,
            )

            if self.optionName in ("day-duration-minutes", "night-duration-minutes"):
                labelText = f"{round(currSetting, 1)}m"
            elif self.optionName in ("fog-density-multiplier", "lighting-intensity", "lighting-exposure", "motion-blur-strength"):
                labelText = f"{round(currSetting * 100)}%"
            elif self.optionName == "ap-reward-text-scale":
                labelText = f"{round(currSetting, 2)}x"
            else:
                labelText = str(round(currSetting * 100))

            self.sliderLabel = DirectLabel(
                parent=self.optionModifier, relief=None, pos=(0.3, 0, -0.01),
                text=labelText, text_scale=0.052,
            )
        else:
            raise Exception(f"Undefined option type: {self.optionType}")

        self.controlTask = ""

    def destroy(self) -> None:
        self.doneRegisterKey()

        self.ignore("controls_findDuplicates")

        if hasattr(self, "dropdownFrame") and self.dropdownFrame is not None:
            self.dropdownFrame.destroy()
            self.dropdownFrame = None

        if hasattr(self, "colorFrame") and self.colorFrame is not None:
            self.colorFrame.destroy()
            self.colorFrame = None

        if hasattr(self, "colorSwatch") and self.colorSwatch is not None:
            self.colorSwatch.destroy()
            self.colorSwatch = None

        if hasattr(self, "sliderLabel"):
            self.sliderLabel.destroy()
            del self.sliderLabel

        self.optionModifier.destroy()
        del self.optionModifier

        self.label.destroy()
        del self.label

        super().destroy()

    @staticmethod
    def formatKeybind(keybind: str) -> str:
        return " ".join(keybind.split("_")).title()

    @staticmethod
    def formatSpeedchat(index: int) -> str:
        return SpeedChatStaticTextToontown[speedChatStyles[index][0]]

    def formatSetting(self, setting: Setting) -> str:
        """Given the type of setting we're dealing with, handle
        how the text on the button will display.
        """
        if isinstance(setting, list):
            # When dealing with list options, strip the brackets and
            # join the parts together.
            if self.optionName == "resolution":
                string = "x"
            else:
                string = ""

            return string.join([str(e) for e in setting]).replace("[", "").replace("[", "")
        elif isinstance(setting, bool):
            return TTLocalizer.OptionEnabled if setting else TTLocalizer.OptionDisabled
        elif isinstance(setting, int):
            # We're most likely dealing with a list of integer settings,
            # so return the string from the localizer given the current setting.
            if self.optionName == "anti-aliasing":
                return TTLocalizer.OptionAntiAlias[setting]
            if self.optionName == "anisotropic-filter":
                return TTLocalizer.OptionAnisotropic[setting]
            if self.optionName == "fps-limit":
                return TTLocalizer.OptionFPSLimit[setting]
            if self.optionName == "battle-speed":
                return TTLocalizer.OptionBattleSpeed[setting]

        if self.optionName in ("shadow-quality", "sky-cloud-quality"):
            return str(setting).title()

        return str(setting)

    def registerKey(self, keybind: str) -> None:
        base.settings.setControl(self.optionName, keybind)
        self.doneRegisterKey()

    def doneRegisterKey(self) -> None:
        self.ignore("controls_stopListening")
        self.ignore(self.controlTask)
        messenger.send("enable-hotkeys")

        self.optionModifier.configure(
            text=self.formatKeybind(base.settings.getControl(self.optionName)),
            image_color=Vec4(1, 1, 1, 1),
        )

        messenger.send("controls_findDuplicates")

    def checkForDuplicates(self) -> None:
        """Iterate through our control schema to find if there are any
        duplicates. In the case that there is, change the button color
        to RED, to indicate that.
        """
        currentKeybind = base.settings.getControl(self.optionName)
        for control, keybind in base.settings.getControls().items():
            # This control is different, but the keybind is the same.
            # Make the button red.
            if control != self.optionName and keybind == currentKeybind:
                self.optionModifier["image_color"] = Vec4(1, 0.1, 0.1, 1)
                return

        # No duplicates were found, keep the color the same.
        self.optionModifier["image_color"] = Vec4(1, 1, 1, 1)

    def _openDropdown(self) -> None:
        buttonPos = self.optionModifier.getPos(aspect2d)
        self.dropdownFrame = DropdownScrolledFrame(
            self.optionName, parent=aspect2d, pos=buttonPos,
            options=self._dropdownOptions(),
            command=self._updateDropdownOption,
            cancelCommand=self._closeDropdown,
        )
        self.dropdownFrame.setBin('gui-popup', 5000)

    @staticmethod
    def _fontOptions():
        return {"archipelago-chat-font", "ap-reward-font"}

    def _dropdownOptions(self):
        """Options shown in the dropdown.  Font options pull the list of
        fonts actually installed on this machine at runtime."""
        if self.optionName in self._fontOptions():
            return ChatTheme.get_installed_fonts()
        return self.optionOptions[self.optionName]

    @staticmethod
    def _autoColorOptions():
        """Colour options that can be set to 'Auto' to follow the accent."""
        return {"archipelago-chat-header-color", "archipelago-chat-channel-ap"}

    def _colorPresets(self):
        return {
            "archipelago-chat-text-color": ChatTheme.TEXT_COLOR_PRESETS,
            "archipelago-chat-bg-color": ChatTheme.BACKGROUND_COLOR_PRESETS,
            "archipelago-chat-accent-color": ChatTheme.ACCENT_COLOR_PRESETS,
            "archipelago-chat-header-color": ChatTheme.ACCENT_COLOR_PRESETS,
            "archipelago-chat-title-color": ChatTheme.TEXT_COLOR_PRESETS,
            "archipelago-chat-channel-ap": ChatTheme.ACCENT_COLOR_PRESETS,
            "archipelago-chat-channel-toon": ChatTheme.ACCENT_COLOR_PRESETS,
            "archipelago-chat-channel-you": ChatTheme.ACCENT_COLOR_PRESETS,
            "archipelago-chat-channel-client": ChatTheme.ACCENT_COLOR_PRESETS,
            "archipelago-chat-channel-error": ChatTheme.TEXT_COLOR_PRESETS,
            "ap-reward-text-color": ChatTheme.TEXT_COLOR_PRESETS,
            "ap-reward-bg-color": ChatTheme.BACKGROUND_COLOR_PRESETS,
        }.get(self.optionName, ChatTheme.TEXT_COLOR_PRESETS)

    def _refreshColorSwatch(self) -> None:
        """Keep the little colour box beside a colour button in sync."""
        if self.optionName not in self._colorPresets():
            return
        if ChatTheme.is_auto_color(base.settings.get(self.optionName)):
            color = Vec4(0.42, 0.42, 0.42, 1)
        else:
            color = parse_hex_color(base.settings.get(self.optionName), Vec4(1, 1, 1, 1))
        if self.colorSwatch is None:
            from direct.gui.DirectGui import DirectFrame
            self.colorSwatch = DirectFrame(
                parent=self, relief=DGG.FLAT, name='colorSwatch',
                frameSize=(-0.026, 0.026, -0.026, 0.026),
                pos=(0.05, 0, self.optionModifier.getZ()),
            )
        self.colorSwatch["frameColor"] = (color[0], color[1], color[2], 1)

    def _openColorPicker(self) -> None:
        if self.optionType != OptionTypes.COLOR:
            return
        buttonPos = self.optionModifier.getPos(aspect2d)
        self.colorFrame = ColorPickerFrame(
            optionName=self.optionName,
            presets=self._colorPresets(),
            onApply=self._updateColorOption,
            cancelCommand=self._closeColorPicker,
            autoEnabled=self.optionName in self._autoColorOptions(),
            pos=buttonPos,
        )
        self.colorFrame.setBin('gui-popup', 5000)

    def _closeColorPicker(self) -> None:
        if getattr(self, 'colorFrame', None) is not None:
            self.colorFrame.destroy()
            self.colorFrame = None

    def _updateColorOption(self, newHex: str) -> None:
        if ChatTheme.is_auto_color(newHex):
            stored = ChatTheme.AUTO_COLOR
        else:
            stored = normalize_hex(newHex, base.settings.get(self.optionName))
        base.settings.set(self.optionName, stored)
        self.optionModifier["text"] = stored
        self._refreshColorSwatch()
        self._applyArchipelagoSettings()
        self._closeColorPicker()

    def _closeDropdown(self) -> None:
        if self.dropdownFrame is not None:
            self.dropdownFrame.destroy()
            self.dropdownFrame = None

    def _updateDropdownOption(self, newSetting) -> None:
        self._closeDropdown()

        # Update the new setting.
        base.settings.set(self.optionName, newSetting)

        if self.optionName in ("resolution", "anisotropic-filter"):
            base.updateDisplay()
        elif self.optionName == "fps-limit":
            if newSetting != 0:
                globalClock.setMode(ClockObject.MLimited)
                globalClock.setFrameRate(newSetting)
            else:
                globalClock.setMode(ClockObject.MNormal)

        if self.optionName in ("shadow-quality", "sky-cloud-quality", "day-night-mode", "lighting-tonemap-mode"):
            self._scheduleOutdoorRefresh()

        if self.optionName in ("archipelago-chat-theme", "archipelago-chat-max-history") or self.optionName in self._fontOptions():
            self._applyArchipelagoSettings()

        # Update the button text with the new setting.
        self.optionModifier["text"] = self.formatSetting(newSetting)

    def _handleAction(self) -> None:
        """Action buttons don't toggle a setting - they do something."""
        if self.optionName == 'arrange-ui':
            from toontown.toontowngui.UIArrangeManager import getUIArrangeManager
            getUIArrangeManager().requestEnter()

    def _updateButtonOption(self) -> None:
        messenger.send("wakeup")

        if self.optionType == OptionTypes.CONTROL:
            # Tell any controls that are listening for input to stop doing that.
            messenger.send("controls_stopListening")
            # Then, listen for that same message on this control in the case
            # of another control being clicked.
            self.accept("controls_stopListening", self.doneRegisterKey)

            self.controlTask = f"{self.optionName}-updateControl"

            self.optionModifier.configure(text='...', image_color=Vec4(0.2, 0.9, 0.9, 1))
            base.buttonThrowers[0].node().setButtonDownEvent(self.controlTask)

            messenger.send("disable-hotkeys")
            self.accept(self.controlTask, self.registerKey)
            return
        
        elif self.optionName == 'refresh-audio':
            base.refreshAudio()
            self.optionModifier["text"] = TTLocalizer.OptionRefresh
            return

        elif self.optionType == OptionTypes.BUTTON_SPEEDCHAT:
            # Increment the speedchat index.
            current = base.localAvatar.getSpeedChatStyleIndex()
            new = current + 1
            if new >= len(speedChatStyles):
                new = 0

            # We handle this differently, as it gets saved on the toon itself.
            base.localAvatar.b_setSpeedChatStyleIndex(new)

            # Update the button text with the new setting.
            self.optionModifier["text"] = self.formatSpeedchat(new)
            return

        # Get the current setting.
        currSetting = base.settings.get(self.optionName)

        # Get the index of the next element of the list of options
        # for this setting.
        index = self.optionOptions[self.optionName].index(currSetting) + 1

        # If it's beyond the scope, set it to 0.
        if index >= len(self.optionOptions[self.optionName]):
            index = 0

        # Index into the options with the new index.
        newSetting = self.optionOptions[self.optionName][index]

        # Update the new setting.
        base.settings.set(self.optionName, newSetting)

        # Update the client with the new value.
        if self.optionName == "music":
            base.enableMusic(newSetting)
        elif self.optionName == "sfx":
            base.enableSoundEffects(newSetting)
        elif self.optionName == "toon-chat-sounds":
            base.toonChatSounds = newSetting
        elif self.optionName == 'competitive-boss-scoring':
            base.localAvatar.wantCompetitiveBossScoring = newSetting
        elif self.optionName == 'archipelago-log-bg':
            base.localAvatar.wantLogBg = newSetting
        elif self.optionName == 'boss-alerts':
            base.localAvatar.wantAlerts = newSetting
        elif self.optionName == 'report-errors':
            os.environ['WANT_ERROR_REPORTING'] = 'true' if newSetting else 'false'
        elif self.optionName in ("borderless", "vertical-sync"):
            base.updateDisplay()
        elif self.optionName == "frame-rate-meter":
            base.setFrameRateMeter(newSetting)
        elif self.optionName == "movement_mode":
            base.localAvatar.updateMovementMode()
        elif self.optionName == "fovEffects":
            base.WANT_FOV_EFFECTS = newSetting
        elif self.optionName == 'discord-rich-presence':
            base.wantRichPresence = newSetting
            base.setRichPresence()
        elif self.optionName == "cam-toggle-lock":
            base.CAM_TOGGLE_LOCK = newSetting
        elif self.optionName == "color-blind-mode":
            base.colorBlindMode = newSetting
        elif self.optionName == "want-legacy-models":
            base.WANT_LEGACY_MODELS = newSetting
        elif self.optionName == "laff-display":
            base.laffMeterDisplay = newSetting
        elif self.optionName == "battle-speed":
            base.battleSpeed = newSetting
        elif self.optionName == "ap-sounds":
            base.apSounds = newSetting
        elif self.optionName == "random-music":
            base.randomMusic = newSetting
            base.refreshRandomMusic()
        elif self.optionName == "random-music-style":
            base.randomMusicStyle = newSetting
            base.refreshRandomMusic()
        elif self.optionName == "new-popup":
            base.newPopup = newSetting

        if self.optionName.startswith("archipelago-"):
            self._applyArchipelagoSettings()

        if self.optionName in {
                "dynamic-shadows", "want-procedural-sky",
                "lighting-bloom-enabled", "lighting-god-rays",
                "lighting-fog-enabled", "want-day-night-cycle",
                "want-water-reflections",
            "motion-blur",
            "motion-blur-strength", "want-aurora-borealis",
                "lighting-contact-shadows", "lighting-streetlamps-enabled"}:
            self._scheduleOutdoorRefresh()

        # Update the button text with the new setting.
        self.optionModifier["text"] = self.formatSetting(newSetting)

    def _updateSliderOption(self) -> None:
        messenger.send("wakeup")

        newSetting = self.optionModifier["value"]

        # Save the new option and update the gui.
        if self.optionName == "music-volume":
            base.musicManager.setVolume(newSetting ** 2)
        elif self.optionName == "sfx-volume":
            for sfm in base.sfxManagerList:
                sfm.setVolume(newSetting ** 2)

        if self.optionName in ("day-duration-minutes", "night-duration-minutes"):
            self.sliderLabel["text"] = f"{round(newSetting, 1)}m"
        elif self.optionName in ("fog-density-multiplier", "lighting-intensity", "lighting-exposure", "motion-blur-strength"):
            self.sliderLabel["text"] = f"{round(newSetting * 100)}%"
        elif self.optionName == "ap-reward-text-scale":
            self.sliderLabel["text"] = f"{round(newSetting, 2)}x"
        else:
            self.sliderLabel["text"] = str(round(newSetting * 100))
        base.settings.set(self.optionName, newSetting)

        if self.optionName.startswith("archipelago-"):
            self._applyArchipelagoSettings()

        if self.optionName in {
                "drop-shadow-strength", "fog-density-multiplier",
                "lighting-intensity", "lighting-exposure",
                "day-duration-minutes", "night-duration-minutes",
                "motion-blur-strength"}:
            self._scheduleOutdoorRefresh()

        if self.optionName == "ap-reward-text-scale":
            self._applyArchipelagoSettings()

    @staticmethod
    def _scheduleOutdoorRefresh() -> None:
        """Coalesce slider drags into one safe world-lighting refresh."""
        taskName = "optionsPageOutdoorLightingRefresh"
        taskMgr.remove(taskName)

        def refresh(task):
            try:
                from toontown.hood import OutdoorLighting
                OutdoorLighting.refreshSettings()
            except Exception:
                pass
            try:
                from toontown.hood import IndoorLighting
                IndoorLighting.refreshSettings()
            except Exception:
                pass
            return task.done

        taskMgr.doMethodLater(0.20, refresh, taskName)

    @staticmethod
    def _applyArchipelagoSettings() -> None:
        avatar = getattr(base, 'localAvatar', None)
        panel = getattr(avatar, 'archipelagoLog', None)
        if panel:
            panel.applySettings()
        reward = getattr(avatar, 'archipelagoRewardDisplay', None)
        if reward:
            reward.applySettings()
