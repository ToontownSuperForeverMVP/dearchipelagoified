"""
UIArrangeManager: lets the player reposition core HUD elements by dragging
them, then saves the results to the settings file.

The draggable elements are:
  * the chat button row (Chat / SpeedChat / Archipelago buttons)
  * the friends list button
  * the laff meter
  * the stickerbook button

Arranging is entered from the Options page ("Arrange UI").  While active,
the stickerbook is closed, the normal actions of the draggable buttons are
disabled, a transparent banner at the top of the screen tells the player to
press ESC to finish, and dragging snaps to the screen edges / to the other
elements so the layout stays tidy.
"""

from panda3d.core import Point3
from direct.task import Task
from direct.gui.DirectGui import DGG, DirectFrame, DirectLabel
from direct.showbase import DirectObject
from direct.showbase.MessengerGlobal import messenger
from direct.directnotify import DirectNotifyGlobal
from toontown.toonbase import ToontownGlobals
from toontown.toonbase import TTLocalizer

_uiArrangeManager = None


def getUIArrangeManager():
    """Return the process-wide UI arrange manager singleton."""
    global _uiArrangeManager
    if _uiArrangeManager is None:
        _uiArrangeManager = UIArrangeManager()
    return _uiArrangeManager


class UIArrangeManager(DirectObject.DirectObject):
    notify = DirectNotifyGlobal.directNotify.newCategory('UIArrangeManager')

    # How close (in aspect2d units) an edge has to be before it snaps.
    SNAP_THRESHOLD = 0.045

    # Minimum half-size (in the element's own units) of the grab overlay, so
    # every element stays easy to grab no matter how small it measures.
    MIN_GRAB_HALF_EXTENT = 0.1

    def __init__(self):
        self._units = []
        self._active = False
        self._banner = None

    # ------------------------------------------------------------------
    # Unit registration
    # ------------------------------------------------------------------

    def registerUnit(self, name, node, settingKey, clickables):
        """Register a draggable HUD element.

        name: unique identifier for this element.
        node: the NodePath that is moved when the element is dragged.
        settingKey: settings key used to persist the position.
        clickables: the DirectGui widgets the player can grab onto.
        """
        # Replace any previous registration with the same name (e.g. after a
        # relogin rebuilt the GUI).
        for i, unit in enumerate(self._units):
            if unit['name'] == name:
                del self._units[i]
                break

        unit = {
            'name': name,
            'node': node,
            'settingKey': settingKey,
            'clickables': clickables,
            'origCommands': {},
            'overlay': None,
            'offset': (0.0, 0.0),
            'dragging': False,
            'taskName': '',
            'extents': (0.0, 0.0),
        }
        self._units.append(unit)
        self._applySavedPosition(unit)
        return unit

    def _applySavedPosition(self, unit):
        saved = base.settings.get(unit['settingKey'])
        if (isinstance(saved, list) and len(saved) == 2 and
                saved[0] is not None and saved[1] is not None):
            unit['node'].setPos(float(saved[0]), 0, float(saved[1]))

    # ------------------------------------------------------------------
    # Entering / exiting arrange mode
    # ------------------------------------------------------------------

    def isActive(self):
        return self._active

    def requestEnter(self):
        """Called from the Options page: enter arrange mode once the
        stickerbook has fully closed (so the book's exit can't fight the
        drag bindings or hide the stickerbook button mid-arrange)."""
        if self._active:
            return
        book = getattr(base.localAvatar, 'book', None)
        if book is not None and getattr(book, 'entered', 0):
            self.acceptOnce('stickerBookExited', self.enter)
            book.closeBook()
        else:
            self.enter()

    def enter(self):
        if self._active:
            return
        self._active = True

        for unit in self._units:
            bounds, extents = self._getBoundsAndExtents(unit)
            unit['extents'] = extents
            unit['origCommands'] = {}

            # Widen the grab area around tiny elements so they stay grabbable.
            minX, maxX, minZ, maxZ = bounds
            centerX = (minX + maxX) / 2.0
            centerZ = (minZ + maxZ) / 2.0
            halfW = max((maxX - minX) / 2.0, self.MIN_GRAB_HALF_EXTENT)
            halfH = max((maxZ - minZ) / 2.0, self.MIN_GRAB_HALF_EXTENT)
            grabBounds = (centerX - halfW, centerX + halfW,
                          centerZ - halfH, centerZ + halfH)

            # Save and disable the normal actions of the underlying widgets so
            # a click never triggers the button's command while arranging.
            for node in unit['clickables']:
                if node is None:
                    continue
                try:
                    unit['origCommands'][id(node)] = node['command']
                    node['command'] = None
                except (KeyError, TypeError):
                    pass

            # A fully transparent grab overlay covers the whole element so it
            # can be grabbed no matter what the underlying widget is (some,
            # like the laff meter, are frames with no clickable region of
            # their own).  It sits above the element, so it also swallows the
            # presses that would otherwise activate the buttons.
            overlay = DirectFrame(
                parent=unit['node'],
                relief=None,
                state=DGG.NORMAL,
                frameSize=grabBounds,
                frameColor=(0, 0, 0, 0),
                sortOrder=DGG.NO_FADE_SORT_INDEX + 10,
            )
            overlay.bind(DGG.B1PRESS, self._startDrag, extraArgs=[unit])
            unit['overlay'] = overlay

        self._showBanner()
        # ESC now exits arrange mode instead of opening the stickerbook.
        self.accept('escape', self.exit)
        base.ignore(base.controls.MAP_PAGE_HOTKEY)
        # Don't let the friends list hotkey pop a panel over the arrangement.
        localAvatar = getattr(base, 'localAvatar', None)
        if localAvatar is not None:
            localAvatar.ignore(ToontownGlobals.FriendsListHotkey)
        self.notify.info('Entered UI arrange mode')

    def exit(self, *args):
        if not self._active:
            return
        self._active = False
        self.ignore('escape')
        self.ignore('mouse1-up')
        # Restore the map-page (ESC) hotkey.
        base.accept(base.controls.MAP_PAGE_HOTKEY, messenger.send,
                    extraArgs=[ToontownGlobals.StickerBookHotkey])
        # Re-sync the friends list button and its hotkey binding.
        localAvatar = getattr(base, 'localAvatar', None)
        if localAvatar is not None and hasattr(localAvatar, 'refreshOnscreenButtons'):
            localAvatar.refreshOnscreenButtons()

        for unit in self._units:
            if unit['overlay'] is not None:
                unit['overlay'].destroy()
                unit['overlay'] = None
            for node in unit['clickables']:
                if node is None:
                    continue
                if id(node) in unit['origCommands']:
                    node['command'] = unit['origCommands'][id(node)]
            unit['origCommands'] = {}
            unit['dragging'] = False
            taskMgr.remove(unit['taskName'])

        self._hideBanner()

        # Persist any positions the player changed.
        for unit in self._units:
            self._savePosition(unit)
        base.settings.write()
        self.notify.info('Exited UI arrange mode')

    # ------------------------------------------------------------------
    # Dragging
    # ------------------------------------------------------------------

    def _startDrag(self, unit, event):
        if not self._active:
            return
        node = unit['node']
        taskMgr.remove(unit['taskName'])
        mouse = event.getMouse()
        aspect = base.getAspectRatio()
        mousePt = Point3(mouse[0] * aspect, 0, mouse[1])
        parent = node.getParent()
        parentMouse = parent.getRelativePoint(aspect2d, mousePt)
        unit['offset'] = (node.getX() - parentMouse.x, node.getZ() - parentMouse.z)
        unit['dragging'] = True
        unit['taskName'] = 'uiArrange-drag-' + unit['name']
        self.acceptOnce('mouse1-up', self._stopDrag, extraArgs=[unit])
        # appendTask=True passes the task object as the last argument, which
        # _dragTask expects; without it the task would raise a TypeError on
        # every frame and the element would never move.
        taskMgr.add(self._dragTask, unit['taskName'], extraArgs=[unit],
                    appendTask=True)

    def _dragTask(self, unit, task):
        if not self._active:
            return Task.done
        watcher = base.mouseWatcherNode
        if watcher.hasMouse():
            mouse = watcher.getMouse()
            aspect = base.getAspectRatio()
            mousePt = Point3(mouse[0] * aspect, 0, mouse[1])
            parent = unit['node'].getParent()
            parentMouse = parent.getRelativePoint(aspect2d, mousePt)
            x = parentMouse.x + unit['offset'][0]
            z = parentMouse.z + unit['offset'][1]
            self._moveUnit(unit, x, z)
        return Task.cont

    def _stopDrag(self, unit, *args):
        taskMgr.remove(unit['taskName'])
        self.ignore('mouse1-up')
        unit['dragging'] = False
        self._savePosition(unit)

    def _savePosition(self, unit):
        node = unit['node']
        base.settings.set(unit['settingKey'],
                          [round(node.getX(), 4), round(node.getZ(), 4)])

    # ------------------------------------------------------------------
    # Positioning, clamping and snapping
    # ------------------------------------------------------------------

    def _moveUnit(self, unit, x, z):
        """Clamp the element on screen and apply edge / alignment snapping."""
        halfW, halfH = unit['extents']
        parent = unit['node'].getParent()
        # The dragged position is in the parent's local space, but the screen
        # bounds are in aspect2d space (the corner nodes are just translations
        # of aspect2d), so convert before clamping.
        center = aspect2d.getRelativePoint(parent, Point3(x, 0, z))
        wx, wz = center.x, center.z
        aspect = max(1.0, base.getAspectRatio())
        wx = min(aspect - halfW, max(-aspect + halfW, wx))
        wz = min(1.0 - halfH, max(-1.0 + halfH, wz))
        local = parent.getRelativePoint(aspect2d, Point3(wx, 0, wz))
        x, z = self._snap(unit, local.x, local.z)
        unit['node'].setPos(x, 0, z)

    def _snap(self, unit, x, z):
        """Snap edges to the screen and align edges/centres with the other
        draggable elements, so a rearranged HUD stays tidy."""
        parent = unit['node'].getParent()
        # The dragged position is in the parent's local space; convert it to
        # aspect2d space so it can be compared against the screen edges and
        # the other elements (which may live under different corner nodes).
        center = aspect2d.getRelativePoint(parent, Point3(x, 0, z))
        wx, wz = center.x, center.z
        halfW, halfH = unit['extents']
        aspect = max(1.0, base.getAspectRatio())
        threshold = self.SNAP_THRESHOLD

        # Snap to the screen edges.
        if abs((wx - halfW) - (-aspect)) <= threshold:
            wx = -aspect + halfW
        elif abs((wx + halfW) - aspect) <= threshold:
            wx = aspect - halfW
        if abs((wz - halfH) - (-1.0)) <= threshold:
            wz = -1.0 + halfH
        elif abs((wz + halfH) - 1.0) <= threshold:
            wz = 1.0 - halfH

        # Align with the other elements (edges and centres on both axes).
        for other in self._units:
            if other is unit:
                continue
            try:
                oNode = other['node']
                if oNode.isEmpty() or oNode.isHidden():
                    continue
                oCenter = oNode.getPos(aspect2d)
                ohw, ohh = other['extents']
            except Exception:
                continue
            if ohw <= 0.0 or ohh <= 0.0:
                continue
            owx, owz = oCenter.x, oCenter.z

            if abs((wx - halfW) - (owx - ohw)) <= threshold:
                wx = owx - ohw + halfW
            elif abs((wx + halfW) - (owx + ohw)) <= threshold:
                wx = owx + ohw - halfW
            elif abs(wx - owx) <= threshold:
                wx = owx
            elif abs((wx - halfW) - (owx + ohw)) <= threshold:
                wx = owx + ohw + halfW
            elif abs((wx + halfW) - (owx - ohw)) <= threshold:
                wx = owx - ohw - halfW

            if abs((wz - halfH) - (owz - ohh)) <= threshold:
                wz = owz - ohh + halfH
            elif abs((wz + halfH) - (owz + ohh)) <= threshold:
                wz = owz + ohh - halfH
            elif abs(wz - owz) <= threshold:
                wz = owz
            elif abs((wz - halfH) - (owz + ohh)) <= threshold:
                wz = owz + ohh + halfH
            elif abs((wz + halfH) - (owz - ohh)) <= threshold:
                wz = owz - ohh - halfH

        local = parent.getRelativePoint(aspect2d, Point3(wx, 0, wz))
        return local.x, local.z

    def _collectNodeBounds(self, np, depth, out):
        """Collect the bounds of np (and its DirectGui children) into out.

        Each entry is (sourceNodePath, minPoint, maxPoint, inNodeSpace).
        The points are expressed in sourceNodePath's own coordinate space;
        inNodeSpace is True when that space is already the element node's
        own space (no conversion needed).

        DirectGui widgets keep their visible/clickable geometry inside the
        PGItem's click-region frame and its state defs.  Neither of those is
        reached by NodePath.calcTightBounds() on the widget (it reports no
        geometry for DirectGui nodes in this engine build), so every source
        has to be collected separately.  State defs are internal nodes, not
        regular scene-graph children, so their bounds must be used as-is and
        never converted with getRelativePoint.
        """
        try:
            pg = np.node()
        except Exception:
            pg = None

        # Sources expressed in np's own coordinate space.
        # 1) The widget's click-region frame (its real grab area).
        if pg is not None:
            try:
                frame = pg.getFrame()
                if frame[0] < frame[1] and frame[2] < frame[3]:
                    out.append((np, Point3(frame[0], 0, frame[2]),
                                Point3(frame[1], 0, frame[3]), True))
            except Exception:
                pass
            # 2) State-def geometry (button images/text live here).
            try:
                for i in range(pg.getNumStateDefs()):
                    sd = pg.getStateDef(i)
                    if sd is None or sd.isEmpty():
                        continue
                    tmin = Point3()
                    tmax = Point3()
                    if sd.calcTightBounds(tmin, tmax):
                        out.append((np, tmin, tmax, True))
            except Exception:
                pass
        # 3) Ordinary geometry parented under the node (models, NodePaths).
        try:
            tmin = Point3()
            tmax = Point3()
            if np.calcTightBounds(tmin, tmax):
                out.append((np, tmin, tmax, True))
        except Exception:
            pass

        # 4) Descendants (e.g. a frame grouping several buttons).  Their
        # geometry lives in their own space, which differs from np's space
        # by the transforms in between, so mark them for conversion.
        if depth > 0:
            try:
                children = np.getChildren()
            except Exception:
                children = []
            for child in children:
                self._collectDescendantBounds(child, depth - 1, out)

    def _collectDescendantBounds(self, np, depth, out):
        """Collect bounds of a descendant widget, in np's own space."""
        try:
            pg = np.node()
        except Exception:
            pg = None

        if pg is not None:
            try:
                frame = pg.getFrame()
                if frame[0] < frame[1] and frame[2] < frame[3]:
                    out.append((np, Point3(frame[0], 0, frame[2]),
                                Point3(frame[1], 0, frame[3]), False))
            except Exception:
                pass
            try:
                for i in range(pg.getNumStateDefs()):
                    sd = pg.getStateDef(i)
                    if sd is None or sd.isEmpty():
                        continue
                    tmin = Point3()
                    tmax = Point3()
                    if sd.calcTightBounds(tmin, tmax):
                        out.append((np, tmin, tmax, False))
            except Exception:
                pass
        try:
            tmin = Point3()
            tmax = Point3()
            if np.calcTightBounds(tmin, tmax):
                out.append((np, tmin, tmax, False))
        except Exception:
            pass

        if depth > 0:
            try:
                children = np.getChildren()
            except Exception:
                children = []
            for child in children:
                self._collectDescendantBounds(child, depth - 1, out)

    def _getBoundsAndExtents(self, unit):
        """Return (local bounds, parent-space half extents) for an element.

        The local bounds (minX, maxX, minZ, maxZ) are used for the grab
        overlay, which is parented to the element itself, so they must be in
        the element's own (scaled) coordinate space.  The parent-space half
        extents are used for snapping; the corner nodes the elements are
        parented to (a2dTopLeft, ...) are plain translations of aspect2d, so
        these extents are directly comparable across elements.
        """
        node = unit['node']
        parent = node.getParent()
        collected = []
        self._collectNodeBounds(node, 2, collected)

        minPt = None
        maxPt = None
        for srcNp, srcMin, srcMax, inNodeSpace in collected:
            if srcMin.x > srcMax.x or srcMin.z > srcMax.z:
                continue
            try:
                if inNodeSpace:
                    lmin, lmax = srcMin, srcMax
                else:
                    # Convert the descendant's bounds into the element's own
                    # space.
                    lmin = node.getRelativePoint(srcNp, srcMin)
                    lmax = node.getRelativePoint(srcNp, srcMax)
            except Exception:
                continue
            if minPt is None:
                minPt = Point3(lmin.x, 0, lmin.z)
                maxPt = Point3(lmax.x, 0, lmax.z)
            else:
                minPt.set(min(minPt.x, lmin.x), 0, min(minPt.z, lmin.z))
                maxPt.set(max(maxPt.x, lmax.x), 0, max(maxPt.z, lmax.z))

        if minPt is None:
            self.notify.warning(
                'UI arrange: could not measure element %r; using a default '
                'grab area' % unit['name'])
            minPt = Point3(-self.MIN_GRAB_HALF_EXTENT, 0,
                           -self.MIN_GRAB_HALF_EXTENT)
            maxPt = Point3(self.MIN_GRAB_HALF_EXTENT, 0,
                           self.MIN_GRAB_HALF_EXTENT)

        bounds = (minPt.x, maxPt.x, minPt.z, maxPt.z)
        pmin = parent.getRelativePoint(node, minPt)
        pmax = parent.getRelativePoint(node, maxPt)
        extents = (abs(pmax.x - pmin.x) / 2.0, abs(pmax.z - pmin.z) / 2.0)
        return bounds, extents

    # ------------------------------------------------------------------
    # Banner
    # ------------------------------------------------------------------

    def _showBanner(self):
        if self._banner is None:
            self._banner = DirectLabel(
                parent=aspect2d,
                relief=DGG.FLAT,
                frameColor=(0, 0, 0, 0.55),
                frameSize=(-1.2, 1.2, -0.06, 0.06),
                text=TTLocalizer.UIArrangeBanner,
                text_scale=0.06,
                text_fg=(1, 1, 1, 1),
                text_shadow=(0, 0, 0, 1),
                sortOrder=DGG.FOREGROUND_SORT_INDEX,
            )
        self._banner.setPos(0, 0, 0.92)
        self._banner.show()

    def _hideBanner(self):
        if self._banner is not None:
            self._banner.hide()
