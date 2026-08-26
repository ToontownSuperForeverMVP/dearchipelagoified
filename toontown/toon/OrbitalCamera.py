from panda3d.core import (BitMask32, CollisionHandlerFloor,
                          CollisionHandlerQueue, CollisionNode, CollisionRay,
                          CollisionSegment, CollisionTraverser, NodePath,
                          Point3, Vec3, WindowProperties)
from direct.directnotify import DirectNotifyGlobal
from direct.fsm.FSM import FSM
from direct.interval.IntervalGlobal import LerpHprInterval, LerpPosHprInterval
from direct.showbase.InputStateGlobal import inputState
from direct.showbase.PythonUtil import fitSrcAngle2Dest, lerp, reduceAngle
from direct.task import Task
from direct.task.TaskManagerGlobal import taskMgr

from otp.otpbase import OTPGlobals
from toontown.toon.CamRunner import CamRunner
from toontown.toon.ParamObj import ParamObj


class OrbitalCamera(FSM, NodePath, ParamObj):
    notify = DirectNotifyGlobal.directNotify.newCategory("OrbitalCamera")

    class ParamSet(ParamObj.ParamSet):
        Params = {"camOffset": Vec3(0, -9, 5.5)}

    UpdateTaskName = "OrbitCamUpdateTask"
    ReadMouseTaskName = "OrbitCamReadMouseTask"
    CollisionCheckTaskName = "OrbitCamCollisionTask"
    MinP = -50
    MaxP = 20
    baseH = None
    minH = None
    maxH = None
    presets = [[-9, 0, 0], [-24, 0, -10], [-12, 0, -15]]

    TopNodeName = "OrbitCam"

    def __init__(self, subject):
        ParamObj.__init__(self)
        NodePath.__init__(self, self.TopNodeName)
        FSM.__init__(self, "OrbitalCamera")

        self.mouseControl = False
        self.mouseDelta = (0, 0)
        self.lastMousePos = (0, 0)
        self.origMousePos = (0, 0)
        self.request("Off")
        self.__inputEnabled = False
        self.subject = subject
        self.mouseX = 0.0
        self.mouseY = 0.0
        self._paramStack = []
        self.setDefaultParams()
        self.presetPos = 0
        self.collisionTaskCount = 0
        self._rmbToken = inputState.watchWithModifiers("RMB", "mouse3")
        self.initializeCollisions()
        self.firstPerson = False
        self.ignoreRMB = False
        self.runner = CamRunner()
        self.cam_toggled = False
        self._smoothedMouseDelta = Vec3(0, 0, 0)
        self._collisionPos = None
        self._presetHprInterval = None
        self._returnInterval = None
        self._transitionOnActive = True

    def destroy(self):
        if self._presetHprInterval:
            self._presetHprInterval.pause()
            self._presetHprInterval = None
        self.destroyCollisions()
        self._rmbToken.release()
        del self._rmbToken
        del self.subject
        FSM.cleanup(self)
        NodePath.removeNode(self)
        ParamObj.destroy(self)
        self.ignoreAll()
    
    def initializeCollisions(self):
        self.cTravOnFloor = CollisionTraverser("CamMode.cTravOnFloor")
        self.camFloorRayNode = self.attachNewNode("camFloorRayNode")
        self.ccRay2 = CollisionRay(0.0, 0.0, 0.0, 0.0, 0.0, -1.0)
        self.ccRay2Node = CollisionNode("ccRay2Node")
        self.ccRay2Node.addSolid(self.ccRay2)
        self.ccRay2NodePath = self.camFloorRayNode.attachNewNode(self.ccRay2Node)
        self.ccRay2BitMask = OTPGlobals.FloorBitmask
        self.ccRay2Node.setFromCollideMask(self.ccRay2BitMask)
        self.ccRay2Node.setIntoCollideMask(BitMask32.allOff())
        self.ccRay2MoveNodePath = hidden.attachNewNode("ccRay2MoveNode")
        self.camFloorCollisionBroadcaster = CollisionHandlerFloor()
        self.camFloorCollisionBroadcaster.setInPattern("zone_on-floor")
        self.camFloorCollisionBroadcaster.setOutPattern("zone_off-floor")
        self.camFloorCollisionBroadcaster.addCollider(
            self.ccRay2NodePath, self.ccRay2MoveNodePath
        )
        self.cTravOnFloor.addCollider(
            self.ccRay2NodePath, self.camFloorCollisionBroadcaster
        )
    
    def destroyCollisions(self):
        del self.cTravOnFloor
        del self.ccRay2
        del self.ccRay2Node
        self.ccRay2NodePath.remove_node()
        del self.ccRay2NodePath
        self.ccRay2MoveNodePath.remove_node()
        del self.ccRay2MoveNodePath
        self.camFloorRayNode.remove_node()
        del self.camFloorRayNode

    def enterActive(self):
        if self.cam_toggled:
            self.cam_toggled = False
        else:
            self.cam_toggled = True
        self.enableInput()

        base.camNode.setLodCenter(self.subject)
        try:
            base.camNode.getLens().setNear(0.35)
        except Exception:
            pass

        self._initMaxDistance()
        self._collisionPos = None
        self._startCollisionCheck()
        if not self.firstPerson:
            self.acceptWheel()
        self.acceptTab()
        if self._returnInterval:
            self._returnInterval.pause()
            self._returnInterval = None
        self.reparentTo(self.subject)
        self.setPos(0, 0, self.subject.getHeight())
        targetPos = Point3(self.camOffset[0], self.camOffset[1], 10)
        targetHpr = Vec3(0, 0, 0)
        if getattr(self, '_transitionOnActive', True) and not base.camera.isEmpty():
            base.camera.wrtReparentTo(self)
            curPos = camera.getPos(self)
            curHpr = camera.getHpr(self)
            if (curPos - targetPos).lengthSquared() > 0.05 or curHpr.lengthSquared() > 0.05:
                self._returnInterval = LerpPosHprInterval(camera, 0.35, targetPos, targetHpr, startPos=curPos, startHpr=curHpr, blendType='easeOut', name='orbitalCameraReturn')
                self._returnInterval.start()
            else:
                base.camera.reparentTo(self)
                camera.setPosHpr(targetPos, targetHpr)
        else:
            base.camera.reparentTo(self)
            camera.setPosHpr(targetPos, targetHpr)

    def _initMaxDistance(self):
        self._maxDistance = abs(self.camOffset[1])

    def exitActive(self):
        self.disableInput()
        self._stopCollisionCheck()
        if self._presetHprInterval:
            self._presetHprInterval.pause()
            self._presetHprInterval = None
        self.ignoreWheel()
        self.ignoreTab()
        if self._returnInterval:
            self._returnInterval.pause()
            self._returnInterval = None

    def enableMouseControl(self, pressed, enabledByMouse=True):
        if not base.CAM_TOGGLE_LOCK:
            self.ignore("InputState-RMB")
            self.accept("InputState-RMB", self.disableMouseControl)
        else:
            self.ignore("InputState-RMB")
            self.accept("InputState-RMB", self.toggleMouseControl)

        if self.oobeEnabled():
            return

        self.mouseControl = True
        mouseData = base.win.getPointer(0)
        self.origMousePos = (mouseData.getX(), mouseData.getY())

        base.win.movePointer(0, base.win.getXSize() // 2, base.win.getYSize() // 2)
        self.lastMousePos = (base.win.getXSize() / 2, base.win.getYSize() / 2)

        if self.getCurrentOrNextState() == "Active":
            self._startMouseControlTasks()
        
        self.setCursor(True)
        self.runner.startInput()

        self.subject.controlManager.setTurn(0)

    def toggleMouseControl(self, pressed):
        if pressed and not self.mouseControl:
            self.enableMouseControl(True, False)
        elif pressed and self.mouseControl:
            self.disableMouseControl(True, True)

    def disableMouseControl(self, pressed, disabledByMouse=True):
        if not base.CAM_TOGGLE_LOCK:
            self.ignore("InputState-RMB")
            self.accept("InputState-RMB", self.enableMouseControl)
        else:
            self.ignore("InputState-RMB")
            self.accept("InputState-RMB", self.toggleMouseControl)

        if self.oobeEnabled():
            return

        if self.mouseControl:
            self.mouseControl = False
            self._smoothedMouseDelta.set(0, 0, 0)
            self._stopMouseControlTasks()

            base.win.movePointer(
                0, int(self.origMousePos[0]), int(self.origMousePos[1])
            )

            base.win.movePointer(
                0, int(self.origMousePos[0]), int(self.origMousePos[1])
            )
            self.setCursor(False)
            self.runner.stopInput()

        self.subject.controlManager.setTurn(1)
    
    def setCursor(self, cursor):
        wp = WindowProperties()
        wp.setCursorHidden(cursor)
        base.win.requestProperties(wp)

    def enableInput(self):
        self.__inputEnabled = True
        self.accept("InputState-RMB", self.enableMouseControl)
        if inputState.isSet("RMB"):
            self.enableMouseControl(True)

    def disableInput(self):
        self.__inputEnabled = False
        self.disableMouseControl(False, False)
        self.ignore("InputState-RMB")

    def isInputEnabled(self):
        return self.__inputEnabled

    def isSubjectMoving(self):
        return any([inputState.isSet(movement) for movement in 
                    ("forward", "reverse", "turnRight", "turnLeft", "slideRight", "slideLeft")])

    def _avatarFacingTask(self, task):
        if self.oobeEnabled():
            return task.cont

        if self.isSubjectMoving():  # or self.subject.isAimingPie:
            camH = self.getH(render)
            subjectH = self.subject.getH(render)
            if abs(camH - subjectH) > 0.01:
                self.subject.setH(render, camH)
                self.setH(0)
        return task.cont

    def _mouseUpdateTask(self, task):
        if self.oobeEnabled():
            return task.cont

        subjectMoving = self.isSubjectMoving()
        subjectTurning = subjectMoving

        if subjectMoving:  # or self.subject.isAimingPie:
            hNode = self.subject
        else:
            hNode = self
        
        camSensitivityX = base.settings.get("camSensitivityX")
        camSensitivityY = base.settings.get("camSensitivityY")

        rawDx, rawDy = self.mouseDelta
        if rawDx or rawDy:
            # A very short, adaptive filter removes pixel stair-stepping at low
            # speeds while large flicks remain virtually one-to-one.
            dt = min(globalClock.getDt(), 0.05)
            magnitude = min(1.0, (abs(rawDx) + abs(rawDy)) / 18.0)
            response = 32.0 + 38.0 * magnitude
            alpha = 1.0 - pow(2.718281828, -response * dt)
            self._smoothedMouseDelta.x += (rawDx - self._smoothedMouseDelta.x) * alpha
            self._smoothedMouseDelta.y += (rawDy - self._smoothedMouseDelta.y) * alpha
            dx, dy = self._smoothedMouseDelta.x, self._smoothedMouseDelta.y
            if subjectTurning:
                dx = +dx
            hNode.setH(hNode, -dx * camSensitivityX)
            curP = self.getP()
            newP = curP + -dy * camSensitivityY
            newP = min(max(newP, self.MinP), self.MaxP)
            self.setP(newP)
            if self.baseH:
                self._checkHBounds(hNode)

            self.setR(render, 0)
        else:
            # Do not let filtering add drift or a sluggish tail after release.
            self._smoothedMouseDelta.set(0, 0, 0)

        return task.cont

    def _checkHBounds(self, hNode):
        currH = fitSrcAngle2Dest(hNode.getH(), 180)
        if currH < self.minH:
            hNode.setH(reduceAngle(self.minH))
        elif currH > self.maxH:
            hNode.setH(reduceAngle(self.maxH))

    def acceptWheel(self):
        self.accept("wheel_up", self._handleWheelUp)
        self.accept("wheel_down", self._handleWheelDown)
        self.accept("page_up", self._handleWheelUp)
        self.accept("page_down", self._handleWheelDown)
        self._resetWheel()

    def ignoreWheel(self):
        self.ignore("wheel_up")
        self.ignore("wheel_down")
        self.ignore("page_up")
        self.ignore("page_down")
        self._resetWheel()
    
    def acceptTab(self):
        self.accept("tab", self.toggleFirstPerson)
    
    def ignoreTab(self):
        self.ignore("tab")
    
    def toggleFirstPerson(self):
        # self.firstPerson = not self.firstPerson
        # if self.firstPerson:
        #     self._handleSetWheel(0)
        #     self.ignoreWheel()
        #     # self.enableMouseControl(True)
        #     # self.ignore("InputState-RMB")
        # else:
        #     self.setPresetPos(0, transition=False)
        #     self.acceptWheel()
        #     # self.disableMouseControl(True)
        self.presetPos += 1
        if self.presetPos >= len(self.presets):
            self.presetPos = 0
        self.setPresetPos(self.presetPos)
    
    def _handleSetWheel(self, y):
        self._collSolid.setPointB(0, y + 1, 0)
        self.camOffset.setY(y)
        t = (-14 - y) / -12
        height = self.subject.getHeight()
        z = lerp(height, height, t)
        self.setZ(z)

    def _handleWheelUp(self):
        y = max(-25, min(-2, self.camOffset[1] + 1.0))
        self._handleSetWheel(y)

    def _handleWheelDown(self):
        y = max(-25, min(-2, self.camOffset[1] - 1.0))
        self._handleSetWheel(y)

    def _resetWheel(self):
        if not self.isActive():
            return

        self.camOffset = Vec3(0, -14, 5.5)
        y = self.camOffset[1]
        z = self.camOffset[2]
        self._collSolid.setPointB(0, y + 1, 0)
        self.setZ(z)

    def getCamOffset(self):
        return self.camOffset

    def setCamOffset(self, camOffset):
        self.camOffset = Vec3(camOffset)

    def applyCamOffset(self):
        if self.isActive():
            camera.setPos(self.camOffset)

    def _setCamDistance(self, distance):
        offset = camera.getPos(self)
        offset.normalize()
        camera.setPos(self, offset * distance)

    def _getCamDistance(self):
        return camera.getPos(self).length()

    def _startCollisionCheck(self):
        self._collSolid = CollisionSegment(0, 0, 0, 0, -(self._maxDistance + 1.0), 0)
        collSolidNode = CollisionNode("OrbitCam.CollSolid")
        collSolidNode.addSolid(self._collSolid)
        collSolidNode.setFromCollideMask(
            OTPGlobals.CameraBitmask
            | OTPGlobals.CameraTransparentBitmask
            | OTPGlobals.FloorBitmask
        )
        collSolidNode.setIntoCollideMask(BitMask32.allOff())
        self._collSolidNp = self.attachNewNode(collSolidNode)
        self._cHandlerQueue = CollisionHandlerQueue()
        self._cTrav = CollisionTraverser("OrbitCam.cTrav")
        self._cTrav.addCollider(self._collSolidNp, self._cHandlerQueue)
        self._collisionPos = Vec3(camera.getPos(self))
        taskMgr.add(
            self._collisionCheckTask, OrbitalCamera.CollisionCheckTaskName, priority=45
        )

    def _collisionCheckTask(self, task=None):
        self.collisionTaskCount = (self.collisionTaskCount + 1) % 5

        if self.oobeEnabled():
            return Task.cont

        if not hasattr(self, '_cTrav') or not self._cTrav:
            return Task.done

        if hasattr(self.subject, 'getGeom'):
            self._cTrav.traverse(self.subject.getGeom())
        elif hasattr(self.subject, 'getGeomNode'):
            self._cTrav.traverse(self.subject.getGeomNode())

        if self.firstPerson or getattr(self.subject, 'isDisguised', False):
            if hasattr(self.subject, 'getGeomNode') and self.subject.getGeomNode():
                self.subject.getGeomNode().hide()
        else:
            if hasattr(self.subject, 'getGeomNode') and self.subject.getGeomNode():
                self.subject.getGeomNode().show()

        collEntry = None
        numEntries = self._cHandlerQueue.getNumEntries()
        if numEntries > 0:
            self._cHandlerQueue.sortEntries()
            for i in range(numEntries):
                entry = self._cHandlerQueue.getEntry(i)
                if entry.hasSurfacePoint():
                    collEntry = entry
                    break

        if not (collEntry and collEntry.hasSurfacePoint()):
            targetPos = Vec3(self.camOffset[0], self.camOffset[1], 0)
            self._moveTowardCollisionTarget(targetPos, blocked=False)

            if not self.firstPerson:
                if getattr(self.subject, 'isDisguised', False):
                    if hasattr(self.subject, 'getGeomNode') and self.subject.getGeomNode():
                        self.subject.getGeomNode().hide()
                else:
                    if hasattr(self.subject, 'getGeomNode') and self.subject.getGeomNode():
                        self.subject.getGeomNode().show()

            return Task.cont

        cNormal = collEntry.getSurfaceNormal(self)
        cPoint = collEntry.getSurfacePoint(self)
        offset = 0.65
        hitPos = cPoint + cNormal * offset
        if hitPos.y > -1.2:
            hitPos.y = -1.2
        hitPos.x = 0
        hitPos.z = 0
        self._moveTowardCollisionTarget(hitPos, blocked=True)
        distance = camera.getDistance(self)
        if not self.firstPerson:
            if distance < 1.8 or getattr(self.subject, 'isDisguised', False):
                if hasattr(self.subject, 'getGeomNode') and self.subject.getGeomNode():
                    self.subject.getGeomNode().hide()
            else:
                if hasattr(self.subject, 'getGeomNode') and self.subject.getGeomNode():
                    self.subject.getGeomNode().show()
        if hasattr(self.subject, 'ccPusherTrav') and self.subject.ccPusherTrav:
            self.subject.ccPusherTrav.traverse(render)
        return Task.cont

    def _moveTowardCollisionTarget(self, targetPos, blocked):
        """Smooth camera-wall motion without making free orbit feel floaty."""
        currentPos = Vec3(camera.getPos(self))
        if self._collisionPos is None or (currentPos - self._collisionPos).lengthSquared() > 4.0:
            self._collisionPos = currentPos
        dt = min(globalClock.getDt(), 0.05)
        # Pull in rapidly for safety; ease back out more gently to prevent the
        # familiar pop when the camera clears a wall edge.
        movingInward = Vec3(targetPos).lengthSquared() < currentPos.lengthSquared()
        response = 34.0 if blocked and movingInward else 15.0
        alpha = 1.0 - pow(2.718281828, -response * dt)
        self._collisionPos += (Vec3(targetPos) - self._collisionPos) * alpha
        camera.setPos(self, self._collisionPos)
    def _stopCollisionCheck(self):
        taskMgr.remove(OrbitalCamera.CollisionCheckTaskName)
        if hasattr(self, '_cTrav') and self._cTrav:
            if hasattr(self, '_collSolidNp') and self._collSolidNp:
                self._cTrav.removeCollider(self._collSolidNp)
            del self._cTrav
        if hasattr(self, '_cHandlerQueue'):
            del self._cHandlerQueue
        if hasattr(self, '_collSolidNp') and self._collSolidNp:
            self._collSolidNp.detachNode()
            del self._collSolidNp
        self._collisionPos = None
        if self.subject:
            if getattr(self.subject, 'isDisguised', False):
                if hasattr(self.subject, 'getGeomNode') and self.subject.getGeomNode():
                    self.subject.getGeomNode().hide()
            else:
                if hasattr(self.subject, 'getGeomNode') and self.subject.getGeomNode():
                    self.subject.getGeomNode().show()

    def setPresetPos(self, presetIndex, transition=True):
        self.presetPos = presetIndex
        self.setCameraPos(
            self.presets[self.presetPos][0],
            self.presets[self.presetPos][1],
            self.presets[self.presetPos][2],
            transition=transition,
        )

    def setCameraPos(self, y, h, p, transition=True):
        t = (-14 - y) / -12
        z = lerp(self.subject.getHeight(), self.subject.getHeight(), t)
        if hasattr(self, '_collSolid') and self._collSolid:
            self._collSolid.setPointB(0, y + 1, 0)
        self.camOffset.setY(y)
        self.setPos(self.getX(), self.getY(), z)
        if self._presetHprInterval:
            self._presetHprInterval.pause()
            self._presetHprInterval = None
        if transition and self.isActive():
            self._presetHprInterval = LerpHprInterval(
                self, 0.24, Vec3(h, p, 0), blendType='easeOut',
                name='orbitCameraPresetHpr')
            self._presetHprInterval.start()
        else:
            self.setHpr(h, p, 0)

    def _startMouseControlTasks(self):
        if self.mouseControl:
            properties = WindowProperties()
            base.win.requestProperties(properties)
            self._startMouseReadTask()
            self._startMouseUpdateTask()

    def _stopMouseControlTasks(self):
        properties = WindowProperties()
        properties.setMouseMode(properties.MAbsolute)
        base.win.requestProperties(properties)
        self._stopMouseReadTask()
        self._stopMouseUpdateTask()

    def _startMouseReadTask(self):
        self._stopMouseReadTask()
        taskMgr.add(
            self._mouseReadTask, self.TopNodeName + "-MouseRead", priority=-29
        )

    def _mouseReadTask(self, task):
        if (self.oobeEnabled()) or not base.mouseWatcherNode.hasMouse():
            self.mouseDelta = (0, 0)
        else:
            winSize = (base.win.getXSize(), base.win.getYSize())
            mouseData = base.win.getPointer(0)
            if mouseData.getX() > winSize[0] or mouseData.getY() > winSize[1]:
                self.mouseDelta = (0, 0)
            else:
                self.mouseDelta = (
                    mouseData.getX() - self.lastMousePos[0],
                    mouseData.getY() - self.lastMousePos[1],
                )
                base.win.movePointer(0, winSize[0] // 2, winSize[1] // 2)

                mouseData = base.win.getPointer(0)
                self.lastMousePos = (mouseData.getX(), mouseData.getY())

        return task.cont

    def _stopMouseReadTask(self):
        taskMgr.remove(self.TopNodeName + "-MouseRead")

    def _startMouseUpdateTask(self):
        self._stopMouseUpdateTask()
        taskMgr.add(
            self._avatarFacingTask,
            self.TopNodeName + "-AvatarFacing",
            priority=23,
        )
        taskMgr.add(
            self._mouseUpdateTask,
            self.TopNodeName + "-MouseUpdate",
            priority=40,
        )

    def _stopMouseUpdateTask(self):
        taskMgr.remove(self.TopNodeName + "-MouseUpdate")
        taskMgr.remove(self.TopNodeName + "-AvatarFacing")

    def start(self, transition=True):
        self._transitionOnActive = transition
        if not self.isActive():
            self.request("Active")
        else:
            if transition and not base.camera.isEmpty():
                targetPos = Point3(self.camOffset[0], self.camOffset[1], 10)
                targetHpr = Vec3(0, 0, 0)
                base.camera.wrtReparentTo(self)
                curPos = camera.getPos(self)
                curHpr = camera.getHpr(self)
                if (curPos - targetPos).lengthSquared() > 0.05 or curHpr.lengthSquared() > 0.05:
                    if hasattr(self, '_returnInterval') and self._returnInterval:
                        self._returnInterval.pause()
                    self._returnInterval = LerpPosHprInterval(camera, 0.35, targetPos, targetHpr, startPos=curPos, startHpr=curHpr, blendType='easeOut', name='orbitalCameraReturn')
                    self._returnInterval.start()

    def stop(self):
        if self.isActive():
            self.request("Off")
            self.subject.setSpeed(0, 0, 0)

    def isActive(self):
        return self.state == "Active"
    
    def oobeEnabled(self):
        return hasattr(base, "oobeMode") and base.oobeMode
