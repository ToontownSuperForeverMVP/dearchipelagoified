import math
import re
import sys
import textwrap
import ctypes
from datetime import datetime
from typing import List, Tuple

from direct.gui.DirectGui import (
    DGG,
    DirectButton,
    DirectEntry,
    DirectFrame,
    DirectLabel,
    DirectScrolledFrame,
)
from direct.showbase.MessengerGlobal import messenger
from direct.interval.FunctionInterval import Func, Wait
from direct.interval.LerpInterval import (LerpColorScaleInterval,
                                          LerpPosInterval,
                                          LerpScaleInterval)
from direct.interval.MetaInterval import Parallel, Sequence
from direct.task import Task
from otp.chat.TalkGlobals import AVATAR_THOUGHT, TALK_OPEN
from panda3d.core import TextNode, TextProperties, TextPropertiesManager, Vec3, Vec4

from toontown.archipelago.apclient.in_game_command_processor import InGameArchipelagoCommandProcessor


class MarkdownFormatter:
    """Translate the commonly used Markdown/GFM syntax into Panda rich text."""

    _PROPERTY_PATTERN = re.compile(r"\x01[^\x01]*\x01|\x02")
    _HEADING_PATTERN = re.compile(r"^(#{1,6})\s*(.+?)\s*#*\s*$")
    _UNORDERED_LIST_PATTERN = re.compile(r"^(\s*)[-+*]\s+(.*)$")
    _ORDERED_LIST_PATTERN = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
    _TASK_PATTERN = re.compile(r"^\[([ xX])\]\s+(.*)$")
    _TABLE_SEPARATOR_PATTERN = re.compile(
        r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
    _LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^\s)]+)(?:\s+['\"][^)]*['\"])?\)")
    _AUTOLINK_PATTERN = re.compile(r"<(https?://[^ >]+)>")

    _STYLE_CODES = {
        "bold": "chat_markdown_bold",
        "italic": "chat_markdown_italic",
        "code": "chat_markdown_code",
        "link": "chat_markdown_link",
        "heading": "chat_markdown_heading",
        "quote": "chat_markdown_quote",
        "rule": "chat_markdown_rule",
    }
    _propertiesRegistered = False

    @classmethod
    def _ensureProperties(cls):
        if cls._propertiesRegistered:
            return
        manager = TextPropertiesManager.getGlobalPtr()

        def register(style, configure):
            properties = TextProperties()
            configure(properties)
            manager.setProperties(cls._STYLE_CODES[style], properties)

        register("bold", lambda properties: properties.setGlyphScale(1.10))
        register("italic", lambda properties: properties.setSlant(0.24))
        register("code", lambda properties: properties.setTextColor(1.0, 0.82, 0.38, 1.0))

        def link(properties):
            properties.setTextColor(0.35, 0.78, 1.0, 1.0)
            properties.setUnderscore(True)
        register("link", link)

        def heading(properties):
            properties.setTextColor(0.92, 0.97, 1.0, 1.0)
            properties.setGlyphScale(1.18)
            properties.setUnderscore(True)
        register("heading", heading)

        def quote(properties):
            properties.setTextColor(0.67, 0.76, 0.86, 1.0)
            properties.setSlant(0.14)
        register("quote", quote)
        register("rule", lambda properties: properties.setTextColor(0.47, 0.68, 0.86, 1.0))
        cls._propertiesRegistered = True

    @classmethod
    def _styled(cls, style, text):
        if not text:
            return ""
        return "\x01%s\x01%s\x02" % (cls._STYLE_CODES[style], text)

    @staticmethod
    def _strikethrough(text):
        """Panda has no strike-through property, so use combining strokes."""
        return "".join(character if character.isspace() else character + "\u0336"
                       for character in text)

    @classmethod
    def _inline(cls, text):
        if not text:
            return ""
        result = []
        index = 0
        while index < len(text):
            if text[index] == "\\" and index + 1 < len(text):
                result.append(text[index + 1])
                index += 2
                continue
            if text[index] == "`":
                end = text.find("`", index + 1)
                if end != -1:
                    result.append(cls._styled("code", text[index + 1:end]))
                    index = end + 1
                    continue
            if text.startswith("~~", index):
                end = text.find("~~", index + 2)
                if end != -1:
                    result.append(cls._strikethrough(cls._inline(text[index + 2:end])))
                    index = end + 2
                    continue
            matched = cls._LINK_PATTERN.match(text, index)
            if matched:
                result.append(cls._styled("link", cls._inline(matched.group(1))))
                index = matched.end()
                continue
            matched = cls._AUTOLINK_PATTERN.match(text, index)
            if matched:
                result.append(cls._styled("link", matched.group(1)))
                index = matched.end()
                continue
            delimiter = None
            for candidate, style in (("**", "bold"), ("__", "bold"),
                                     ("*", "italic"), ("_", "italic")):
                if text.startswith(candidate, index):
                    if (candidate.startswith("_") and
                            ((index > 0 and text[index - 1].isalnum()) or
                             (index + len(candidate) < len(text) and
                              text[index + len(candidate)].isspace()))):
                        continue
                    end = text.find(candidate, index + len(candidate))
                    if end != -1 and end > index + len(candidate):
                        delimiter = (candidate, style, end)
                        break
            if delimiter:
                marker, style, end = delimiter
                result.append(cls._styled(style, cls._inline(text[index + len(marker):end])))
                index = end + len(marker)
                continue
            result.append(text[index])
            index += 1
        return "".join(result)

    @classmethod
    def render(cls, message):
        """Render headings, quotes, lists, code blocks, links, and inline GFM."""
        cls._ensureProperties()
        lines = str(message).splitlines() or [""]
        rendered = []
        inCodeBlock = False
        for line in lines:
            if line.strip().startswith("```"):
                inCodeBlock = not inCodeBlock
                continue
            if inCodeBlock:
                rendered.append(cls._styled("code", line))
                continue
            heading = cls._HEADING_PATTERN.match(line)
            if heading:
                rendered.append(cls._styled("heading", cls._inline(heading.group(2))))
                continue
            if re.match(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", line):
                rendered.append(cls._styled("rule", "────────────────"))
                continue
            if cls._TABLE_SEPARATOR_PATTERN.match(line):
                rendered.append(cls._styled("rule", "────────────────"))
                continue
            if "|" in line:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                rendered.append(" │ ".join(cls._inline(cell) for cell in cells))
                continue
            if line.startswith(">"):
                rendered.append(cls._styled("quote", "│ " + cls._inline(line[1:].lstrip())))
                continue
            unordered = cls._UNORDERED_LIST_PATTERN.match(line)
            if unordered:
                indent, body = unordered.groups()
                task = cls._TASK_PATTERN.match(body)
                if task:
                    mark, body = task.groups()
                    bullet = "☑ " if mark.lower() == "x" else "☐ "
                else:
                    bullet = "• "
                rendered.append(indent + bullet + cls._inline(body))
                continue
            ordered = cls._ORDERED_LIST_PATTERN.match(line)
            if ordered:
                indent, number, body = ordered.groups()
                rendered.append(indent + number + ". " + cls._inline(body))
                continue
            rendered.append(cls._inline(line))
        return "\n".join(rendered)

    @classmethod
    def renderMessage(cls, message, channel):
        """Keep a Toon's name plain while rendering the Markdown message body."""
        message = str(message)
        if channel == "TOON":
            sender, separator, body = message.partition(": ")
            if separator and sender:
                return sender + separator + cls.render(body)
        return cls.render(message)

    @classmethod
    def plainText(cls, message):
        """Return readable text for sizing without Panda property markers."""
        text = cls._PROPERTY_PATTERN.sub("", str(message))
        text = cls._LINK_PATTERN.sub(r"\1", text)
        text = re.sub(r"(?<!\\)(\*\*|__|~~|`|\*|_)", "", text)
        return text.replace("\\*", "*").replace("\\_", "_")


def _writeClipboard(text):
    """Place Unicode text on the native clipboard, with a safe non-Windows fallback."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = (ctypes.c_uint, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    kernel32.GlobalFree.argtypes = (ctypes.c_void_p,)
    user32.SetClipboardData.argtypes = (ctypes.c_uint, ctypes.c_void_p)
    user32.SetClipboardData.restype = ctypes.c_void_p
    if not user32.OpenClipboard(None):
        return False
    memory = None
    try:
        user32.EmptyClipboard()
        encoded = ctypes.create_unicode_buffer(str(text))
        memory = kernel32.GlobalAlloc(0x0002, ctypes.sizeof(encoded))
        if not memory:
            return False
        destination = kernel32.GlobalLock(memory)
        if not destination:
            return False
        ctypes.memmove(destination, encoded, ctypes.sizeof(encoded))
        kernel32.GlobalUnlock(memory)
        if not user32.SetClipboardData(13, memory):
            return False
        memory = None  # Clipboard now owns this handle.
        return True
    finally:
        if memory:
            kernel32.GlobalFree(memory)
        user32.CloseClipboard()


def _readClipboard():
    """Read Unicode text from the native clipboard without shelling out."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetClipboardData.argtypes = (ctypes.c_uint,)
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    if not user32.OpenClipboard(None):
        return None
    try:
        memory = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not memory:
            return None
        source = kernel32.GlobalLock(memory)
        if not source:
            return None
        try:
            return ctypes.wstring_at(source)
        finally:
            kernel32.GlobalUnlock(memory)
    finally:
        user32.CloseClipboard()


class ArchipelagoOnscreenLog(DirectFrame):
    """A persistent, draggable chat stream shared by Toontown and Archipelago."""

    THEMES = {
        "Blue": ((0.08, 0.55, 0.88, 1), (0.04, 0.25, 0.39, 1)),
        "Green": ((0.10, 0.72, 0.48, 1), (0.04, 0.30, 0.22, 1)),
        "Purple": ((0.58, 0.35, 0.86, 1), (0.23, 0.12, 0.38, 1)),
        "Orange": ((0.94, 0.48, 0.13, 1), (0.39, 0.19, 0.05, 1)),
    }
    PROPERTY_PATTERN = re.compile(r"\x01[^\x01]*\x01|\x02")
    HEADER_HEIGHT = 0.085
    INPUT_BASE_HEIGHT = 0.096
    INPUT_LINE_HEIGHT = 0.049
    MAX_INPUT_LINES = 12
    MIN_MESSAGE_HEIGHT = 0.15
    PANEL_PADDING = 0.025
    # How close (in aspect2d units) the panel must be to a screen edge before
    # it snaps flush during a drag for that magnetic feel.
    SNAP_THRESHOLD = 0.045

    def __init__(self):
        DirectFrame.__init__(
            self,
            parent=aspect2d,
            relief=DGG.FLAT,
            sortOrder=DGG.FOREGROUND_SORT_INDEX - 5,
        )

        self._messages: List[Tuple[str, str, str]] = []
        self._messageRows: List[DirectFrame] = []
        self._selectedMessageRow = None
        self._selectedMessageText = ""
        self._selectedMessageBaseColor = None
        self._clipboardFallback = ""
        self._nextMessageZ = 0.0
        self._rowSerial = 0
        self._unreadCount = 0
        self._inputFocused = False
        self._dragging = False
        self._dragTaskName = self.taskName("drag")
        self._dragOffset = (0.0, 0.0)
        # Drag-to-resize grip state.
        self._resizing = False
        self._resizeTaskName = self.taskName("resize")
        self._resizeStart = (0.0, 0.0)
        self._resizeStartDims = (0.0, 0.0)
        self._resizeTopLeft = (0.0, 0.0)
        self._gripHoverScale = 1.0
        self._nextMessageRebuild = None
        # Names a message may use to address the local player; computed lazily
        # so it keeps up if they rename mid-session.
        self._mentionNames = None
        # Subtle chat sound effects, loaded once and reused.
        self._sfxChatIn = base.loader.loadSfx(
            "phase_3.5/audio/sfx/GUI_whisper_3.ogg")
        self._sfxChatOut = base.loader.loadSfx(
            "phase_3.5/audio/sfx/GUI_quicktalker.ogg")
        self._sfxOpen = base.loader.loadSfx(
            "phase_3/audio/sfx/GUI_balloon_popup.ogg")
        self._panelSequence = None
        self._buttonIntervals = {}
        self._commandHistory = []
        self._historyIndex = 0
        self._historyDraft = ""
        # When True, new messages keep the view pinned to the newest entry.
        # Scrolling away from the bottom turns this off so history stays put.
        self._autoScroll = True
        # Number of lines the composer input currently displays (grown to fit
        # the typed text, plus one line of headroom).
        self._inputLines = 1
        self._width = 0.94
        self._height = 0.72
        self._accent = self.THEMES["Blue"][0]
        self._accentDark = self.THEMES["Blue"][1]

        self.header = DirectFrame(parent=self, relief=DGG.FLAT, state=DGG.NORMAL)
        self.header.bind(DGG.B1PRESS, self._startDrag)

        self.accentStrip = DirectFrame(parent=self, relief=DGG.FLAT)
        self.apIcon = DirectLabel(
            parent=self.header,
            relief=None,
            image="phase_14/maps/ap_icon.png",
            image_scale=0.030,
        )

        self.title = DirectLabel(
            parent=self.header,
            relief=None,
            text="CHAT",
            text_align=TextNode.ALeft,
            text_scale=0.043,
            text_fg=(1, 1, 1, 1),
            text_shadow=(0, 0, 0, 0.45),
        )
        self.connectionButton = DirectButton(
            parent=self.header,
            relief=DGG.FLAT,
            text="AP SETUP",
            text_scale=0.029,
            text_fg=(1, 1, 1, 1),
            command=self._toggleConnectionPanel,
        )
        self.closeButton = DirectButton(
            parent=self.header,
            relief=DGG.FLAT,
            text="-",
            text_scale=0.046,
            text_fg=(1, 1, 1, 1),
            command=self.hideAllEntries,
        )

        self.messageFrame = DirectScrolledFrame(
            parent=self,
            relief=DGG.FLAT,
            state=DGG.NORMAL,
            borderWidth=(0, 0),
            autoHideScrollBars=True,
            manageScrollBars=True,
            scrollBarWidth=0.025,
            verticalScroll_relief=DGG.FLAT,
            verticalScroll_thumb_relief=DGG.FLAT,
            verticalScroll_incButton_relief=None,
            verticalScroll_decButton_relief=None,
        )
        # Let the mouse wheel scroll the message history no matter what part
        # of the log the cursor happens to be over.
        self._bindWheel(self.messageFrame)
        self._bindWheel(self.messageFrame.verticalScroll)
        self._bindWheel(self.messageFrame.verticalScroll.thumb)
        self._bindWheel(self.messageFrame.verticalScroll.incButton)
        self._bindWheel(self.messageFrame.verticalScroll.decButton)
        # Fires only on real user drags/clicks of the scrollbar, letting us
        # unpin from the bottom when the player scrolls up to read history.
        self.messageFrame.verticalScroll["command"] = self._onScrollBarAdjusted

        self.inputBackdrop = DirectFrame(parent=self, relief=DGG.FLAT)
        self.input = DirectEntry(
            parent=self,
            relief=DGG.FLAT,
            text="",
            text_align=TextNode.ALeft,
            text_scale=0.040,
            text_fg=(0.96, 0.97, 1, 1),
            cursorKeys=True,
            numLines=2,
            focus=0,
            backgroundFocus=0,
            command=self._submit,
            focusInCommand=self._focusInput,
            focusOutCommand=self._blurInput,
        )
        # Grow/shrink the composer to match the text as it is typed or erased.
        self.input.bind(DGG.TYPE, self._onInputChanged)
        self.input.bind(DGG.ERASE, self._onInputChanged)
        self.sendButton = DirectButton(
            parent=self,
            relief=DGG.FLAT,
            text="SEND",
            text_scale=0.036,
            text_fg=(1, 1, 1, 1),
            command=self._submit,
            clickSound=self._sfxChatOut,
        )
        # Corner grip used to resize the panel on the fly without the options
        # page. Kept minimal: a quiet little handle at the bottom-right.
        self.resizeGrip = DirectFrame(parent=self, relief=DGG.FLAT, state=DGG.NORMAL)
        self.resizeGrip.bind(DGG.B1PRESS, self._startResize)
        self.resizeGrip.bind(DGG.B1RELEASE, self._stopResize)
        self.resizeGrip.bind(DGG.B3PRESS, self._startResize)
        self.resizeGrip.bind(DGG.B3RELEASE, self._stopResize)
        self.resizeGrip.bind(DGG.WITHIN, self._gripHover,
                             extraArgs=[1.35])
        self.resizeGrip.bind(DGG.WITHOUT, self._gripHover,
                             extraArgs=[1.0])
        # A tiny diagonal trio of dots hints at draggability without adding
        # weight to the minimal design.
        self._gripDots = []
        dotSize = 0.0045
        for offset in ((-0.018, 0.018), (0.0, 0.0), (0.018, -0.018)):
            dot = DirectLabel(
                parent=self, relief=DGG.FLAT,
                frameColor=(1, 1, 1, 0.55),
                frameSize=(-dotSize, dotSize, -dotSize, dotSize),
            )
            dot.setPos(offset[0], 0, offset[1])
            self._gripDots.append(dot)
        self.applySettings(initial=True)
        self._commandProcessor = InGameArchipelagoCommandProcessor(self, base.localAvatar)
        self._bindAnimatedButton(self.connectionButton)
        self._bindAnimatedButton(self.closeButton)
        self._bindAnimatedButton(self.sendButton)
        self.accept("NewOpenMessage", self._handleToonMessage)
        self.accept("arrow_up", self._historyPrevious)
        self.accept("arrow_down", self._historyNext)
        self.accept("page_up", self._pageUp)
        self.accept("page_down", self._pageDown)
        self.accept("escape", self._handleEscape)
        self.accept("control-c", self._copyToClipboard)
        self.accept("control-v", self._pasteFromClipboard)

    def destroy(self):
        self._stopDrag()
        self._stopResize()
        self._blurInput()
        self._cleanupAnimations()
        self._destroyMessageRows()
        for dot in self._gripDots:
            dot.destroy()
        self._gripDots = []
        self.resizeGrip.destroy()
        if self._sfxChatIn:
            self._sfxChatIn.stop()
        if self._sfxChatOut:
            self._sfxChatOut.stop()
        if self._sfxOpen:
            self._sfxOpen.stop()
        self._sfxChatIn = None
        self._sfxChatOut = None
        self._sfxOpen = None
        self.connectionButton.destroy()
        self.closeButton.destroy()
        self.apIcon.destroy()
        self.title.destroy()
        self.header.destroy()
        self.accentStrip.destroy()
        self.input.destroy()
        self.sendButton.destroy()
        self.inputBackdrop.destroy()
        self.messageFrame.destroy()
        DirectFrame.destroy(self)

    def addToLog(self, msg):
        self._appendMessage(msg, "AP")

    def addCommandEcho(self, msg):
        self._appendMessage(msg, "YOU")

    def addCommandOutput(self, msg, error=False):
        self._appendMessage(msg, "ERROR" if error else "CLIENT")

    def _appendMessage(self, msg, channel):
        if msg is None:
            return

        timestamp = datetime.now().strftime("%H:%M")
        message = str(msg)
        isMention = self._isMention(message)
        self._messages.append((timestamp, message, channel))
        self._trimHistory()
        row = self._appendMessageRow(timestamp, message, channel, isMention=isMention)
        self._updateMessageCanvas()
        # Only chase the newest message when the player is already pinned to
        # the bottom; otherwise respect where they are in the history.
        if self._autoScroll:
            self._scrollToBottom()
        self._animateMessageRow(row)
        if channel == "AP":
            self._pulseAccent()

        # A short, unobtrusive blip for incoming messages (typing our own chat
        # is already its own audio event). A subtle click plays for commands
        # that produce a direct reply, so they never feel silent. Mentions get
        # a touch more presence so an addressed message never slips by.
        if self._animationsEnabled() and self._soundsEnabled():
            if isMention:
                self._playChatInSound(volume=0.7)
            elif channel == "AP":
                self._playChatInSound()
            elif channel in ("CLIENT", "ERROR"):
                self._playChatInSound(volume=0.45)

        if self.isHidden():
            if channel == "AP" and base.settings.get("archipelago-chat-auto-show"):
                self.showAllEntries()
            else:
                self._unreadCount += 1
                self._updateTitle()

    def _handleToonMessage(self, talkMessage):
        if talkMessage.getTalkType() not in (TALK_OPEN, AVATAR_THOUGHT):
            return
        body = talkMessage.getBody()
        if not body:
            return
        sender = talkMessage.getSenderAvatarName()
        if not sender:
            sender = "Toon"
        self._appendMessage("%s: %s" % (sender, body), "TOON")

    def applySettings(self, initial=False, rebuildMessages=True,
                      preserveDimensions=False):
        """Apply all unified chat settings immediately without recreating it.

        ``rebuildMessages`` may be passed ``False`` during live drags to keep
        resizing responsive; the full rebuild happens on release. During a
        resize, ``preserveDimensions`` keeps the dimensions calculated from
        the cursor instead of reloading the previously saved settings.
        """
        if not preserveDimensions:
            self._width = self._numberSetting("archipelago-chat-width", 0.94, 0.20, float("inf"))
            self._height = self._numberSetting("archipelago-chat-height", 0.72, 0.16, float("inf"))
        opacity = self._numberSetting("archipelago-chat-opacity", 0.82, 0.15, 1.0)
        theme = base.settings.get("archipelago-chat-theme")
        self._accent, self._accentDark = self.THEMES.get(theme, self.THEMES["Blue"])

        backgroundEnabled = bool(base.settings.get("archipelago-log-bg"))
        bodyAlpha = opacity if backgroundEnabled else 0.0
        self["frameColor"] = (0.018, 0.025, 0.045, bodyAlpha)
        self["frameSize"] = (-self._width / 2, self._width / 2, -self._height / 2, self._height / 2)

        top = self._height / 2
        bottom = -self._height / 2
        halfWidth = self._width / 2
        self.header["frameColor"] = self._withAlpha(self._accentDark, max(0.78, opacity))
        self.header["frameSize"] = (-halfWidth, halfWidth, -self.HEADER_HEIGHT / 2, self.HEADER_HEIGHT / 2)
        self.header.setPos(0, 0, top - self.HEADER_HEIGHT / 2)
        self.accentStrip["frameColor"] = self._withAlpha(self._accent, 0.95)
        self.accentStrip["frameSize"] = (-0.006, 0.006, -self._height / 2, self._height / 2)
        self.accentStrip.setPos(-halfWidth + 0.006, 0, 0)
        self.apIcon.setPos(-halfWidth + 0.047, 0, 0)
        self.title.setPos(-halfWidth + 0.110, 0, -0.012)
        self.connectionButton["frameColor"] = self._withAlpha(self._accent, 0.86)
        self.connectionButton["frameSize"] = (-0.090, 0.090, -0.028, 0.028)
        self.connectionButton.setPos(halfWidth - 0.150, 0, 0)
        self.closeButton["frameColor"] = (1, 1, 1, 0.10)
        self.closeButton["frameSize"] = (-0.025, 0.025, -0.025, 0.025)
        self.closeButton.setPos(halfWidth - 0.028, 0, 0)

        self.inputBackdrop["frameColor"] = (0.025, 0.035, 0.060, max(0.70, opacity))
        self.input["frameColor"] = (0, 0, 0, 0)
        self.sendButton["frameColor"] = self._withAlpha(self._accent, 0.95)
        self.messageFrame["frameColor"] = (0.02, 0.03, 0.052, bodyAlpha * 0.72)
        self.messageFrame["verticalScroll_frameColor"] = self._withAlpha(self._accentDark, 0.85)
        self.messageFrame["verticalScroll_thumb_frameColor"] = self._withAlpha(self._accent, 0.95)
        self._updateComposerLayout(rebuildMessages=rebuildMessages)

        # Resize has a dedicated slot beside SEND, so their hit targets never
        # overlap. It remains available even when position dragging is locked.
        gripSize = 0.027
        self.resizeGrip["frameColor"] = self._withAlpha(self._accentDark, 0.88)
        self.resizeGrip["frameSize"] = (-gripSize, gripSize, -gripSize, gripSize)
        self.resizeGrip.setPos(*self._resizeGripPos)
        for dot, offset in zip(self._gripDots,
                               ((-0.018, 0.018), (0.0, 0.0), (0.018, -0.018))):
            dot.setPos(self._resizeGripPos[0] + offset[0], 0,
                       self._resizeGripPos[2] + offset[1])

        if initial:
            saved = base.settings.get("archipelago-chat-position")
            if isinstance(saved, list) and len(saved) == 2:
                self.setPos(float(saved[0]), 0, float(saved[1]))
            else:
                self.setPos(-1.24, 0, 0.50)

        self._clampPosition(saveSetting=True)
        self._trimHistory()

    def _writeResizeSetting(self):
        if not hasattr(base, "settings"):
            return
        base.settings.set("archipelago-chat-width", round(self._width, 4))
        base.settings.set("archipelago-chat-height", round(self._height, 4))
        base.settings.write()

    def _gripHover(self, targetScale, _event):
        self._gripHoverScale = targetScale
        for dot in self._gripDots:
            dot.setScale(targetScale * 0.9, 1, targetScale * 0.9)

    def _startResize(self, event):
        taskMgr.remove(self._resizeTaskName)
        self.acceptOnce("mouse1-up", self._stopResize)
        self.acceptOnce("mouse3-up", self._stopResize)
        self._resizeStart = event.getMouse()
        self._resizeStartDims = (self._width, self._height)
        self._resizeTopLeft = (self.getX() - self._width / 2,
                               self.getZ() + self._height / 2)
        self._resizing = True
        self._nextMessageRebuild = None
        taskMgr.add(self._resizeTask, self._resizeTaskName)

    def _resizeTask(self, task):
        watcher = base.mouseWatcherNode
        if not watcher.hasMouse():
            return Task.cont
        mouse = watcher.getMouse()
        # Resize by cursor delta from the initial grab, rather than treating
        # the grip's centre as the panel corner. This keeps the panel steady
        # when the grip is separated from SEND and avoids a jump on press.
        topleftX, topleftZ = self._resizeTopLeft
        newW = self._resizeStartDims[0] + (mouse[0] - self._resizeStart[0]) * base.getAspectRatio()
        newH = self._resizeStartDims[1] - (mouse[1] - self._resizeStart[1])
        newW = max(0.20, newW)
        newH = max(0.16, newH)
        if abs(newW - self._width) < 0.0005 and abs(newH - self._height) < 0.0005:
            return Task.cont

        self._width = newW
        self._height = newH
        # Keep the top-left corner visually fixed while sizing so the panel
        # grows/shrinks from the corner being dragged.
        self.setPos(topleftX + self._width / 2, 0, topleftZ - self._height / 2)

        # Rebuild the message rows at most ~every tenth of a second so the
        # drag stays smooth on large histories.
        now = task.time
        if self._nextMessageRebuild is None:
            self._nextMessageRebuild = now + 0.10
            self.applySettings(rebuildMessages=True, preserveDimensions=True)
        else:
            self.applySettings(rebuildMessages=False, preserveDimensions=True)
            if now >= self._nextMessageRebuild:
                self._nextMessageRebuild = now + 0.10
                self._rebuildMessages(scrollToBottom=self._autoScroll)
        self._clampPosition(saveSetting=False)
        return Task.cont

    def _stopResize(self, *_):
        taskMgr.remove(self._resizeTaskName)
        self.ignore("mouse1-up")
        self.ignore("mouse3-up")
        if not self._resizing:
            return
        self._resizing = False
        self._gripHoverScale = 1.0
        # Keep the final drag dimensions while laying out and persisting them;
        # reloading settings here would restore the pre-drag size.
        self.applySettings(rebuildMessages=True, preserveDimensions=True)
        self._writeResizeSetting()




    def _onInputChanged(self, *_):
        """Keep the composer sized to its text as the player types or erases."""
        self._updateComposerLayout()

    def _composerLineCount(self):
        """Number of wrapped lines the composer text currently needs."""
        try:
            text = self.input.get() if hasattr(self, "input") else ""
        except Exception:
            text = ""
        try:
            wordwrap = max(8, int(round(float(self.input["width"]))))
        except Exception:
            wordwrap = 40
        return max(1, self._estimateLines(text, wordwrap))

    def _updateComposerLayout(self, rebuildMessages=False):
        """Lay out the composer and message area, growing the input box
        upward from the bottom of the panel to fit the wrapped text."""
        top = self._height / 2
        bottom = -self._height / 2
        halfWidth = self._width / 2
        sendWidth = 0.115
        gripSlotWidth = 0.070
        inputLeft = -halfWidth + self.PANEL_PADDING
        inputRight = halfWidth - self.PANEL_PADDING - gripSlotWidth - sendWidth - 0.012
        viewTop = top - self.HEADER_HEIGHT - 0.015

        self.input["width"] = max(1, (inputRight - inputLeft - 0.040) / 0.040)

        # Keep one line of headroom so PGEntry always accepts the keystroke
        # that wraps the text onto a new line before we resize to match.
        maxBySpace = int((viewTop - bottom - 0.012 - 0.026
                          - self.MIN_MESSAGE_HEIGHT - self.INPUT_BASE_HEIGHT)
                         / self.INPUT_LINE_HEIGHT) + 1
        numLines = min(self._composerLineCount() + 1, self.MAX_INPUT_LINES,
                       max(2, maxBySpace))
        changed = numLines != self._inputLines
        self._inputLines = numLines

        inputHeight = self.INPUT_BASE_HEIGHT + (numLines - 1) * self.INPUT_LINE_HEIGHT
        inputZ = bottom + 0.012 + inputHeight / 2

        self.inputBackdrop["frameSize"] = (inputLeft, inputRight,
                                           -inputHeight / 2, inputHeight / 2)
        self.inputBackdrop.setPos(0, 0, inputZ)
        self.input["numLines"] = numLines
        self.input["frameSize"] = (0, max(0.1, inputRight - inputLeft - 0.03),
                                   -inputHeight / 2 + 0.012, inputHeight / 2 - 0.012)
        self.input.setPos(inputLeft + 0.015, 0, inputZ)
        self.sendButton["frameSize"] = (-sendWidth / 2, sendWidth / 2,
                                        -inputHeight / 2, inputHeight / 2)
        self.sendButton.setPos(halfWidth - self.PANEL_PADDING - gripSlotWidth - sendWidth / 2,
                               0, inputZ)
        self._resizeGripPos = (halfWidth - self.PANEL_PADDING - gripSlotWidth / 2,
                               0, inputZ)

        viewBottom = bottom + 0.012 + inputHeight
        viewHeight = max(self.MIN_MESSAGE_HEIGHT, viewTop - viewBottom)
        frameLeft = -halfWidth + self.PANEL_PADDING
        frameRight = halfWidth - self.PANEL_PADDING
        self.messageFrame["frameSize"] = (frameLeft, frameRight, 0, viewHeight)
        self.messageFrame.setPos(0, 0, viewBottom)

        if rebuildMessages or changed:
            self._rebuildMessages(scrollToBottom=self._autoScroll)

    def _caretLine(self):
        """0-based wrapped line the composer caret is currently on."""
        try:
            text = self.input.get()
            pos = int(self.input.guiItem.getCursorPosition())
        except Exception:
            return 0
        pos = max(0, min(pos, len(text)))
        try:
            wordwrap = max(8, int(round(float(self.input["width"]))))
        except Exception:
            wordwrap = 40
        lines = []
        for paragraph in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(paragraph, width=wordwrap,
                                       replace_whitespace=False,
                                       drop_whitespace=False) or [""])
        index = 0
        for lineIndex, line in enumerate(lines):
            if pos <= index + len(line):
                return lineIndex
            index += len(line) + 1
        return len(lines) - 1

    def toggle(self):
        if self.isHidden():
            self.showAllEntries()
        else:
            self.hideAllEntries()

    def openComposer(self):
        self.showAllEntries()
        self.focusComposer()

    def focusComposer(self):
        if self.isHidden():
            self.showAllEntries()
        self.input["focus"] = 1

    def showAllEntries(self):
        self._unreadCount = 0
        self._updateTitle()
        if not self.isHidden():
            self._scrollToBottom()
            return
        self._cleanupPanelSequence()
        destination = self.getPos()
        start = destination + Vec3(-0.10, 0, 0)
        self.setPos(start)
        self.setScale(0.985)
        self.setColorScale(1, 1, 1, 0)
        self.show()
        self._panelSequence = Parallel(
            LerpPosInterval(self, 0.20, destination, startPos=start, blendType="easeOut"),
            LerpScaleInterval(self, 0.20, 1.0, startScale=0.985, blendType="easeOut"),
            LerpColorScaleInterval(self, 0.14, (1, 1, 1, 1),
                                   startColorScale=(1, 1, 1, 0)),
        )
        self._panelSequence.start()
        self._scrollToBottom()
        if self._soundsEnabled():
            self._playSfx(self._sfxOpen, volume=0.65)

    def hideAllEntries(self):
        if self.isHidden():
            return
        self._blurInput()
        self._cleanupPanelSequence()
        destination = self.getPos()
        end = destination + Vec3(-0.08, 0, 0)
        self._panelSequence = Sequence(
            Parallel(
                LerpPosInterval(self, 0.15, end, startPos=destination, blendType="easeIn"),
                LerpScaleInterval(self, 0.15, 0.985, startScale=self.getScale(), blendType="easeIn"),
                LerpColorScaleInterval(self, 0.12, (1, 1, 1, 0),
                                       startColorScale=self.getColorScale()),
            ),
            Func(self.hide),
            Func(self.setPos, destination),
            Func(self.setScale, 1.0),
            Func(self.setColorScale, 1, 1, 1, 1),
        )
        self._panelSequence.start()

    def _copyToClipboard(self):
        """Copy the focused composer or the selected chat row with Ctrl+C."""
        if self.isHidden():
            return
        if self._inputFocused:
            text = self.input.get()
        else:
            text = self._selectedMessageText
        if not text:
            return
        self._clipboardFallback = str(text)
        _writeClipboard(text)

    def _pasteFromClipboard(self):
        """Paste clipboard text at the composer's caret with Ctrl+V."""
        if self.isHidden():
            return
        pasted = _readClipboard()
        if pasted is None:
            pasted = self._clipboardFallback
        if not pasted:
            return
        self.focusComposer()
        existing = self.input.get()
        try:
            cursor = int(self.input.guiItem.getCursorPosition())
        except Exception:
            cursor = len(existing)
        cursor = min(len(existing), max(0, cursor))
        self.input.enterText(existing[:cursor] + pasted + existing[cursor:])
        self.input.setCursorPosition(cursor + len(pasted))
        self._updateComposerLayout()

    def _submit(self, *_):
        raw = self.input.get().strip()
        route, message = self._routeOutgoingMessage(raw)
        if not route or not message:
            return
        self._rememberCommand(raw)
        if route == "CLIENT":
            self._commandProcessor.process(message)
        elif route == "AP":
            self.addCommandEcho(message)
            base.localAvatar.sendArchipelagoChat(message)
        elif route == "BOTH":
            # Combined chat: the message is echoed back into this log as a
            # TOON entry when the server broadcasts it, so no local echo.
            base.localAvatar.sendArchipelagoChat(message)
            chatMgr = getattr(base.localAvatar, "chatMgr", None)
            if chatMgr and hasattr(chatMgr, "sendUnifiedToonChat"):
                chatMgr.sendUnifiedToonChat(message)
        else:
            chatMgr = getattr(base.localAvatar, "chatMgr", None)
            if chatMgr and hasattr(chatMgr, "sendUnifiedToonChat"):
                chatMgr.sendUnifiedToonChat(message)
        self.input.enterText("")
        self._updateComposerLayout()
        self.input["focus"] = 1

    def _routeOutgoingMessage(self, message):
        """Choose a destination without making the player switch chat modes.

        Plain text goes to both local Toontown chat and the Archipelago
        server, combining the two chats into one. Archipelago server
        commands keep their familiar leading ``!``; TextClient/Tracker
        commands use ``/``. ``/ap`` forces a message to Archipelago only,
        and ``/toon`` forces it to Toontown only (useful when a message
        intentionally starts with ! or /).  Magic words are local dev
        commands, so they never reach the Archipelago server.
        """
        message = str(message).strip()
        if not message:
            return None, ""

        lowered = message.lower()
        for prefix in ("/ap", "/archipelago"):
            if lowered == prefix:
                return "AP", ""
            if lowered.startswith(prefix + " "):
                return "AP", message[len(prefix):].lstrip()

        for prefix in ("/toon", "/tt"):
            if lowered == prefix:
                return "TOON", ""
            if lowered.startswith(prefix + " "):
                return "TOON", message[len(prefix):].lstrip()

        if self._isMagicWord(message):
            # Magic words run locally (and are never broadcast as Toon chat
            # either), so sending them to the Archipelago server would just
            # leak dev commands into the multiworld.
            return "TOON", message

        if message.startswith("/"):
            return "CLIENT", message
        if message.startswith("!"):
            return "AP", message
        return "BOTH", message

    def _isMagicWord(self, message):
        """True if the message would be handled locally as a magic word."""
        try:
            if not base.cr.wantMagicWords:
                return False
        except Exception:
            return False
        prefix = '~'
        try:
            index = base.settings.get('magic-word-activator')
            allowed = ['~', '?', '/', '<', ':', ';']
            if not base.config.GetBool('exec-chat', 0):
                allowed.append('>')
            if 0 <= index <= len(allowed) - 1:
                prefix = allowed[index]
        except Exception:
            pass
        return message.startswith(prefix)

    def _rememberCommand(self, value):
        value = str(value).strip()
        if value and (not self._commandHistory or self._commandHistory[-1] != value):
            self._commandHistory.append(value)
            del self._commandHistory[:-50]
        self._historyIndex = len(self._commandHistory)
        self._historyDraft = ""

    def _historyPrevious(self):
        if not self._inputFocused or not self._commandHistory:
            return
        if self._caretLine() > 0:
            # Caret can still move up inside the text; let the entry handle it
            # instead of replacing what was typed.
            return
        if self._historyIndex == len(self._commandHistory):
            self._historyDraft = self.input.get()
        self._historyIndex = max(0, self._historyIndex - 1)
        self.input.enterText(self._commandHistory[self._historyIndex])
        self._updateComposerLayout()

    def _historyNext(self):
        if not self._inputFocused or not self._commandHistory:
            return
        if self._caretLine() < self._composerLineCount() - 1:
            # Caret can still move down inside the text; let the entry handle
            # it instead of replacing what was typed.
            return
        self._historyIndex = min(len(self._commandHistory), self._historyIndex + 1)
        if self._historyIndex == len(self._commandHistory):
            self.input.enterText(self._historyDraft)
        else:
            self.input.enterText(self._commandHistory[self._historyIndex])
        self._updateComposerLayout()

    def _handleEscape(self):
        if not self._inputFocused:
            return
        if self.input.get():
            self.input.enterText("")
            self._updateComposerLayout()
        else:
            self._blurInput()

    def _bindAnimatedButton(self, button):
        button.bind(DGG.WITHIN, self._buttonHover, extraArgs=[button, 1.06])
        button.bind(DGG.WITHOUT, self._buttonHover, extraArgs=[button, 1.0])
        button.bind(DGG.B1PRESS, self._buttonHover, extraArgs=[button, 0.95])
        button.bind(DGG.B1RELEASE, self._buttonHover, extraArgs=[button, 1.04])

    def _buttonHover(self, button, targetScale, _event):
        old = self._buttonIntervals.pop(id(button), None)
        if old:
            old.finish()
        interval = LerpScaleInterval(button, 0.09, targetScale,
                                     startScale=button.getScale(), blendType="easeOut")
        self._buttonIntervals[id(button)] = interval
        interval.start()

    def _animateMessageRow(self, row):
        if row is None or self.isHidden():
            return
        if not self._animationsEnabled():
            row.setColorScale(1, 1, 1, 1)
            return
        destination = row.getPos()
        start = destination + Vec3(-0.035, 0, 0)
        row.setPos(start)
        row.setColorScale(1, 1, 1, 0)
        Sequence(
            Parallel(
                LerpPosInterval(row, 0.18, destination, startPos=start, blendType="easeOut"),
                LerpColorScaleInterval(row, 0.13, (1, 1, 1, 1),
                                       startColorScale=(1, 1, 1, 0)),
            )
        ).start()

    def _pulseAccent(self):
        if not self._animationsEnabled():
            return
        self.accentStrip.clearColorScale()
        Sequence(
            LerpColorScaleInterval(self.accentStrip, 0.10, (1.35, 1.35, 1.35, 1)),
            LerpColorScaleInterval(self.accentStrip, 0.28, (1, 1, 1, 1)),
        ).start()

    def _animationsEnabled(self):
        try:
            return bool(base.settings.get("archipelago-chat-animations", True))
        except Exception:
            return True

    def _soundsEnabled(self):
        try:
            return bool(base.settings.get("archipelago-chat-sounds", True))
        except Exception:
            return True

    def _playSfx(self, sfx, volume=1.0):
        if not sfx:
            return
        try:
            sfx.setVolume(volume)
            sfx.play()
        except Exception:
            pass

    def _playChatInSound(self, volume=0.5):
        self._playSfx(self._sfxChatIn, volume=volume)

    def _cleanupPanelSequence(self):
        if self._panelSequence:
            self._panelSequence.finish()
            self._panelSequence = None

    def _cleanupAnimations(self):
        self._cleanupPanelSequence()
        for interval in self._buttonIntervals.values():
            interval.finish()
        self._buttonIntervals = {}

    def _focusInput(self):
        if self._inputFocused:
            return
        self._inputFocused = True
        messenger.send("disable-hotkeys")
        avatar = getattr(base, "localAvatar", None)
        if avatar:
            avatar.disableControls()
        self._focusPulse(True)

    def _blurInput(self):
        wasFocused = self._inputFocused
        self._inputFocused = False
        if hasattr(self, "input") and self.input["focus"]:
            self.input["focus"] = 0
        self._focusPulse(False)
        if not wasFocused:
            return
        messenger.send("enable-hotkeys")
        avatar = getattr(base, "localAvatar", None)
        if avatar:
            avatar.enableControls()

    def _focusPulse(self, on):
        """Gently accent the send button while the composer is focused so the
        active state reads even in the minimal theme (off when animations are
        disabled)."""
        if not self._animationsEnabled():
            self.sendButton.setColorScale(1, 1, 1, 1)
            return
        if on:
            self.sendButton.clearColorScale()
            Sequence(
                LerpColorScaleInterval(self.sendButton, 0.18,
                                       (1.18, 1.18, 1.18, 1),
                                       blendType="easeInOut"),
                LerpColorScaleInterval(self.sendButton, 0.28,
                                       (1.0, 1.0, 1.0, 1),
                                       blendType="easeInOut"),
            ).start()
        else:
            self.sendButton.setColorScale(1, 1, 1, 1)

    def _toggleConnectionPanel(self):
        avatar = getattr(base, "localAvatar", None)
        chatMgr = getattr(avatar, "chatMgr", None)
        if chatMgr and hasattr(chatMgr, "toggleArchipelagoConnectionPanel"):
            chatMgr.toggleArchipelagoConnectionPanel()

    def _startDrag(self, event):
        if base.settings.get("archipelago-chat-position-lock"):
            return
        taskMgr.remove(self._dragTaskName)
        mouse = event.getMouse()
        aspect = base.getAspectRatio()
        self._dragOffset = (self.getX() - mouse[0] * aspect, self.getZ() - mouse[1])
        self._dragging = True
        self.acceptOnce("mouse1-up", self._stopDrag)
        taskMgr.add(self._dragTask, self._dragTaskName)

    def _dragTask(self, task):
        watcher = base.mouseWatcherNode
        if watcher.hasMouse():
            mouse = watcher.getMouse()
            self.setPos(mouse[0] * base.getAspectRatio() + self._dragOffset[0], 0,
                        mouse[1] + self._dragOffset[1])
            self._clampPosition(saveSetting=False)
            self._snapPosition()
        return Task.cont

    def _snapPosition(self):
        """Snap the panel flush to a nearby screen edge for a magnetic feel.
        Each edge is pulled to its screen boundary (left/right/top/bottom)
        once the panel comes within SNAP_THRESHOLD of it."""
        try:
            if not base.settings.get("archipelago-chat-snap", True):
                return
        except Exception:
            return
        aspect = max(1.0, base.getAspectRatio())
        halfW = self._width / 2
        halfH = self._height / 2
        x, _, z = self.getPos()
        threshold = self.SNAP_THRESHOLD

        # Left edge -> screen left (x - halfW == 0)
        if abs((x - halfW) - 0.0) <= threshold:
            x = halfW
        # Right edge -> screen right (x + halfW == aspect)
        elif abs((x + halfW) - aspect) <= threshold:
            x = aspect - halfW
        # Top edge -> screen top (z + halfH == 1)
        if abs((z + halfH) - 1.0) <= threshold:
            z = 1.0 - halfH
        # Bottom edge -> screen bottom (z - halfH == -1)
        elif abs((z - halfH) - -1.0) <= threshold:
            z = -1.0 + halfH

        self.setPos(x, 0, z)

    def _stopDrag(self, *_):
        taskMgr.remove(self._dragTaskName)
        self.ignore("mouse1-up")
        wasDragging = self._dragging
        self._dragging = False
        if not hasattr(base, "settings"):
            return
        # Run the snap once more so the saved position is the snapped one even
        # if the mouse released off the threshold pause.
        self._snapPosition()
        self._clampPosition(saveSetting=True)
        if wasDragging:
            base.settings.write()

    def _clampPosition(self, saveSetting):
        aspect = max(1.0, base.getAspectRatio())
        xLimit = max(0.0, aspect - self._width / 2)
        zLimit = max(0.0, 1.0 - self._height / 2)
        x = min(xLimit, max(-xLimit, self.getX()))
        z = min(zLimit, max(-zLimit, self.getZ()))
        self.setPos(x, 0, z)
        if saveSetting:
            base.settings.set("archipelago-chat-position", [round(x, 4), round(z, 4)])

    def _trimHistory(self):
        try:
            limit = int(base.settings.get("archipelago-chat-max-history"))
        except (TypeError, ValueError):
            limit = 250
        limit = min(500, max(50, limit))
        if len(self._messages) > limit:
            removeCount = len(self._messages) - limit
            del self._messages[:removeCount]
            for row in self._messageRows[:removeCount]:
                row.destroy()
            del self._messageRows[:removeCount]

    def _destroyMessageRows(self):
        for row in self._messageRows:
            row.destroy()
        self._messageRows = []
        self._selectedMessageRow = None
        self._selectedMessageText = ""
        self._selectedMessageBaseColor = None
        self._nextMessageZ = 0.0

    def _messageLayout(self):
        halfWidth = self._width / 2
        left = -halfWidth + self.PANEL_PADDING + 0.018
        right = halfWidth - self.PANEL_PADDING - 0.035
        availableWidth = max(0.25, right - left)
        textSizeSetting = self._numberSetting("archipelago-textsize", 0.5, 0.0, 1.0)
        textScale = 0.040 + (textSizeSetting * 0.016)
        timestamps = bool(base.settings.get("archipelago-chat-timestamps"))
        timestampWidth = 0.105 if timestamps else 0.0
        messageWidth = max(0.18, availableWidth - timestampWidth - 0.015)
        channelWidth = 0.090
        messageWidth = max(0.18, messageWidth - channelWidth)
        wordwrap = max(8, int(messageWidth / textScale))
        estimatedCharacters = max(12, int(wordwrap * 2.0))
        return (halfWidth, left, right, textScale, timestamps,
                timestampWidth, channelWidth, wordwrap, estimatedCharacters)

    def _mentionNameSet(self):
        """Case-insensitive names the local player goes by, used to spot
        messages that address them directly. Cached once per session since a
        player's own name doesn't change while logged in."""
        if self._mentionNames is not None:
            return self._mentionNames
        names = set()
        try:
            avatar = base.localAvatar
            name = avatar.getName()
            if name:
                names.add(name.lower())
                first = name.split()[0]
                if first:
                    names.add(first.lower())
            part = getattr(avatar, "playerInfo", None)
            if part is not None and getattr(part, "playerName", None):
                names.add(part.playerName.lower())
        except Exception:
            pass
        names.discard("")
        self._mentionNames = names
        return names

    def _isMention(self, message):
        """True if the message addresses the local player by name."""
        try:
            if not base.settings.get("archipelago-chat-mentions", True):
                return False
        except Exception:
            return False
        plain = self.PROPERTY_PATTERN.sub("", str(message))
        content = re.sub(r"[^\w@]+", " ", plain).lower()
        for token in self._mentionNameSet():
            if token and len(token) >= 2 and token in content.split():
                return True
        return False

    def _appendMessageRow(self, timestamp, message, channel, layout=None, isMention=None):
        if layout is None:
            layout = self._messageLayout()
        (_, left, right, textScale, timestamps, timestampWidth,
         channelWidth, wordwrap, estimatedCharacters) = layout

        if not self._messageRows:
            viewHeight = self.messageFrame["frameSize"][3] - self.messageFrame["frameSize"][2]
            self._nextMessageZ = viewHeight - 0.018

        if isMention is None:
            isMention = self._isMention(message)
        displayMessage = MarkdownFormatter.renderMessage(message, channel)
        lineCount = self._estimateLines(MarkdownFormatter.plainText(message),
                                        estimatedCharacters)
        rowHeight = max(0.065, lineCount * textScale * 1.32 + 0.024)
        row = DirectFrame(
            parent=self.messageFrame.getCanvas(),
            relief=DGG.FLAT,
            frameColor=(self._withAlpha(self._accent, 0.22)
                        if isMention
                        else (1, 1, 1, 0.035 if self._rowSerial % 2 == 0 else 0.015)),
            frameSize=(left, right, -rowHeight, 0),
            pos=(0, 0, self._nextMessageZ),
        )
        self._rowSerial += 1
        row._chatBaseColor = row["frameColor"]
        # A thin accent bar down the left edge marks mention rows at a glance.
        if isMention:
            DirectFrame(
                parent=row,
                relief=DGG.FLAT,
                frameColor=self._withAlpha(self._accent, 0.9),
                frameSize=(left + 0.004, left + 0.011, -rowHeight, 0),
            )
        # Rows cover the message area, so they need their own wheel bindings
        # for the cursor to scroll the log while over any message.
        self._bindWheel(row)
        if timestamps:
            DirectLabel(
                parent=row,
                relief=None,
                text=timestamp,
                text_align=TextNode.ALeft,
                text_scale=max(0.028, textScale * 0.78),
                text_fg=(0.55, 0.64, 0.73, 1),
                pos=(left + 0.012, 0, -textScale - 0.008),
            )
        channelColors = {
            "AP": self._accent,
            "TOON": (0.16, 0.72, 0.88, 1),
            "YOU": (0.95, 0.72, 0.18, 1),
            "CLIENT": (0.55, 0.38, 0.92, 1),
            "ERROR": (0.92, 0.23, 0.28, 1),
        }
        channelColor = channelColors.get(channel, self._accent)
        channelLabel = DirectLabel(
            parent=row,
            relief=DGG.FLAT,
            frameColor=self._withAlpha(channelColor, 0.28),
            frameSize=(-0.040, 0.040, -0.017, 0.017),
            text=channel,
            text_align=TextNode.ACenter,
            text_scale=0.021 if channel == "CLIENT" else (0.024 if channel == "TOON" else 0.027),
            text_fg=(0.90, 0.97, 1, 1),
            pos=(left + timestampWidth + channelWidth / 2, 0, -textScale - 0.002),
        )
        messageLabel = DirectLabel(
            parent=row,
            relief=None,
            text=displayMessage,
            text_align=TextNode.ALeft,
            text_scale=textScale,
            text_wordwrap=wordwrap,
            text_fg=(self._withAlpha(self._accent, 0.85)
                     if isMention else (0.96, 0.97, 1, 1)),
            text_shadow=(0, 0, 0, 0.55),
            pos=(left + timestampWidth + channelWidth + 0.012, 0, -textScale - 0.008),
        )
        self._bindMessageSelection(row, message, row, channelLabel, messageLabel)
        self._messageRows.append(row)
        self._nextMessageZ -= rowHeight + 0.008
        return row

    def _bindMessageSelection(self, row, message, *widgets):
        """Make a chat row visibly selected and copyable without UI buttons."""
        for widget in widgets:
            widget.bind(DGG.B1PRESS, self._selectMessage,
                        extraArgs=[row, message])

    def _selectMessage(self, row, message, _event):
        self._blurInput()
        if self._selectedMessageRow and self._selectedMessageRow != row:
            try:
                self._selectedMessageRow["frameColor"] = self._selectedMessageBaseColor
            except Exception:
                pass
        self._selectedMessageRow = row
        self._selectedMessageText = self.PROPERTY_PATTERN.sub("", str(message))
        self._selectedMessageBaseColor = row._chatBaseColor
        row["frameColor"] = self._withAlpha(self._accent, 0.34)

    def _updateMessageCanvas(self):
        halfWidth = self._width / 2
        viewHeight = self.messageFrame["frameSize"][3] - self.messageFrame["frameSize"][2]
        if self._messageRows:
            contentTop = self._messageRows[0].getZ() + 0.018
            contentBottom = min(self._nextMessageZ - 0.008, contentTop - viewHeight)
        else:
            contentTop = viewHeight
            contentBottom = 0.0
        self.messageFrame["canvasSize"] = (
            -halfWidth + self.PANEL_PADDING,
            halfWidth - self.PANEL_PADDING,
            contentBottom,
            contentTop,
        )

    def _rebuildMessages(self, scrollToBottom):
        if not hasattr(self, "messageFrame"):
            return
        pinned = self._autoScroll or scrollToBottom
        self._destroyMessageRows()
        self._rowSerial = 0
        layout = self._messageLayout()
        for timestamp, message, channel in self._messages:
            self._appendMessageRow(timestamp, message, channel, layout)
        self._updateMessageCanvas()
        if pinned:
            self._scrollToBottom()
        else:
            # Rebuilds reset the canvas, so keep whatever follow state the
            # player had before the rebuild.
            self._autoScroll = self._isAtBottom()

    def _scrollToBottom(self):
        self._autoScroll = True
        if hasattr(self, "messageFrame"):
            self.messageFrame["verticalScroll_value"] = 1.0

    # ------------------------------------------------------------------
    # Message history scrolling
    # ------------------------------------------------------------------

    def _bindWheel(self, widget):
        if widget is None:
            return
        widget.bind(DGG.WHEELUP, self._wheelUp)
        widget.bind(DGG.WHEELDOWN, self._wheelDown)

    def _wheelUp(self, _event=None):
        self._scrollBy(-1, page=False)

    def _wheelDown(self, _event=None):
        self._scrollBy(1, page=False)

    def _pageUp(self):
        if self._inputFocused or self.isHidden() or not self._mouseOverPanel():
            return
        self._scrollBy(-1, page=True)

    def _pageDown(self):
        if self._inputFocused or self.isHidden() or not self._mouseOverPanel():
            return
        self._scrollBy(1, page=True)

    def _mouseOverPanel(self):
        """Whether the cursor is inside the chat panel (aspect2d space)."""
        try:
            watcher = base.mouseWatcherNode
            if not watcher.hasMouse():
                return False
            mouse = watcher.getMouse()
            x = mouse[0] * base.getAspectRatio()
            z = mouse[1]
        except Exception:
            return False
        return (abs(x - self.getX()) <= self._width / 2 and
                abs(z - self.getZ()) <= self._height / 2)

    def _scrollableViewHeight(self):
        if not hasattr(self, "messageFrame"):
            return 0.0
        frameSize = self.messageFrame["frameSize"]
        return max(0.0, frameSize[3] - frameSize[2])

    def _scrollableCanvasHeight(self):
        if not hasattr(self, "messageFrame"):
            return 0.0
        canvasSize = self.messageFrame["canvasSize"]
        return max(0.0, canvasSize[3] - canvasSize[2])

    def _canScroll(self):
        return self._scrollableCanvasHeight() > self._scrollableViewHeight() + 0.001

    def _isAtBottom(self):
        """True when the newest messages are visible (or there is nothing to scroll)."""
        if not self._canScroll():
            return True
        try:
            value = self.messageFrame.verticalScroll.getValue()
        except Exception:
            value = self.messageFrame["verticalScroll_value"]
        return value >= 1.0 - 0.01

    def _scrollBy(self, direction, page):
        """Scroll the log by a page (direction -1 = up/older, 1 = down/newer)."""
        if not hasattr(self, "messageFrame") or self.isHidden():
            return
        viewHeight = self._scrollableViewHeight()
        canvasHeight = self._scrollableCanvasHeight()
        scrollable = max(0.0001, canvasHeight - viewHeight)
        if page:
            units = 0.85 * viewHeight
        else:
            units = max(0.04 * viewHeight, 3 * (self._messageLayout()[3] * 1.32 + 0.022))
        value = self.messageFrame["verticalScroll_value"]
        value = min(1.0, max(0.0, value + direction * units / scrollable))
        self.messageFrame["verticalScroll_value"] = value
        self._autoScroll = self._isAtBottom()

    def _onScrollBarAdjusted(self, *_):
        # User dragged the thumb or clicked the bar: re-evaluate whether we
        # should keep following new messages.
        self._autoScroll = self._isAtBottom()

    def _updateTitle(self):
        if self._unreadCount:
            self.title["text"] = "CHAT - %s NEW" % self._unreadCount
        else:
            self.title["text"] = "CHAT"

    @classmethod
    def _estimateLines(cls, message, wordwrap):
        plain = cls.PROPERTY_PATTERN.sub("", message).replace("\t", "    ")
        lines = 0
        for paragraph in plain.splitlines() or [""]:
            lines += max(1, len(textwrap.wrap(paragraph, width=wordwrap,
                                             replace_whitespace=False,
                                             drop_whitespace=False)))
        return max(1, lines)

    @staticmethod
    def _numberSetting(name, default, low, high):
        try:
            value = float(base.settings.get(name))
            if not math.isfinite(value):
                raise ValueError
        except (TypeError, ValueError):
            value = default
        return min(high, max(low, value))

    @staticmethod
    def _withAlpha(color, alpha):
        return Vec4(color[0], color[1], color[2], alpha)
