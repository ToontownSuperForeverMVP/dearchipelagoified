from typing import List
import random

from direct.gui import DirectGuiGlobals
from direct.gui.DirectButton import DirectButton
from direct.gui.DirectFrame import DirectFrame
from direct.gui.DirectLabel import DirectLabel
from direct.gui.DirectWaitBar import DirectWaitBar
from direct.gui.OnscreenText import OnscreenText
from direct.interval.FunctionInterval import Wait, Func
from direct.interval.LerpInterval import LerpPosInterval, LerpFunctionInterval
from direct.interval.MetaInterval import Sequence
from panda3d.core import ClockObject, TextNode, TransparencyAttrib
from direct.gui.DirectGui import DGG

from toontown.archipelago.definitions.rewards import APReward, IgnoreReward


# A wrapper class for an AP reward that was obtained, stores the APReward and the person who gave it to us
# specifically used to the client can display who gave us the item
class APRewardGift:

    def __init__(self, reward: APReward, gifter: str, isLocal: bool):
        self.reward: APReward = reward
        self.gifter: str = gifter
        self.isLocal: bool = isLocal

    def get_display_string(self) -> str:
        return self.reward.get_reward_string(self.gifter, self.isLocal)

    def get_image_path(self) -> str:
        return self.reward.get_image_path()

    def shouldDisplay(self) -> bool:
        return type(self.reward) is not IgnoreReward

    def get_image_scale(self) -> float:
        return self.reward.get_image_scale()

    def get_image_pos(self):
        return self.reward.get_image_pos()


class ArchipelagoRewardDisplay(DirectLabel):

    FRAME_WIDTH = 0.9
    FRAME_HEIGHT = 0.2
    FRAME_COLOR = (0.1, 0.1, 0.1, 0.9)

    ONSCREEN_POS = (0, 0, -0.6)
    OFFSCREEN_POS = (-0.9, 0, -0.6)

    IMAGE_SCALE = 0.08
    IMAGE_POS = (.12, 0, .1)

    TEXT_SCALE = .035
    TEXT_POS = (0.55, 0.138)
    TEXT_COLOR = (1, 1, 1, 1)
    TEXT_ALIGN = TextNode.ACenter

    SHOW_DURATION = 10.0
    SLIDE_DURATION = .25

    DEFAULT_IMAGE_PATH = 'phase_14/maps/ap_icon.png'

    # Escalating count sounds: normal, then large, massive, and wondrous as items arrive in a row
    ITEM_SOUND_PATHS = [
        'phase_14/audio/sfx/count.wav',
        'phase_14/audio/sfx/count_large.wav',
        'phase_14/audio/sfx/count_massive.wav',
        'phase_14/audio/sfx/count_wondrous.wav',
    ]
    # A pentatonic-ish set of playback rates so repeated rewards sound like a pleasant count
    ITEM_SOUND_PITCHES = [0.8, 0.9, 1.0, 1.12, 1.25]
    # If items are received within this window of each other, the count sound escalates a tier
    COMBO_WINDOW = 3.0
    # On the top tier, the pitch climbs each successive item, then loops back to normal pitch
    # before it gets uncomfortably high
    WONDROUS_PITCH_START = 1.0
    WONDROUS_PITCH_STEP = 0.1
    WONDROUS_PITCH_MAX = 1.5

    def __init__(self, **kw):
        super().__init__(**kw)
        self.initialiseoptions(ArchipelagoRewardDisplay)

        self.close_button = DirectButton(parent=self, frameColor=(.9, 0.1, 0.1, 1), scale=.25, pos=(0.875, 0, 0.175), command=self._do_hide_sequence)
        self.additional_items_label = OnscreenText(parent=self.close_button, align=TextNode.ACenter, text='x', fg=(1, 1, 1, 1), scale=.2, pos=(-0.01, -.055), mayChange=True)
        self.close_all_tip = OnscreenText(parent=self.close_button, align=TextNode.ACenter, text='Shift+Click to Clear', fg=(1, 1, 1, 1), bg=(0, 0, 0, 0.5), scale=.17, pos=(-0.01, 0.18), mayChange=True)
        self.showtime_bar = DirectWaitBar(parent=self, range=100, value=100, frameColor=(0, 0, 0, 0), barColor=(1, 1, 1, 1), frameSize=(0, self.FRAME_WIDTH, 0, self.FRAME_HEIGHT * .02))
        self.close_button.bind(DGG.WITHIN, self.showCloseTip)
        self.close_button.bind(DGG.WITHOUT, self.hideCloseTip)
        self.close_all_tip.hide()
        self._reward_queue: List[APRewardGift] = []
        self._slide_sequence = None
        self.__holding_shift = False
        self.accept('shift', self.__shift_press)
        self.accept('shift-up', self.__shift_up)
        self.item_sounds = [base.loader.loadSfx(path) for path in self.ITEM_SOUND_PATHS]
        self._count_tier = 0
        self._wondrous_pitch = self.WONDROUS_PITCH_START
        self._last_item_time = None

    def __shift_press(self):
        self.__holding_shift = True

    def __shift_up(self):
        self.__holding_shift = False

    def showCloseTip(self, event):
        self.close_all_tip.show()

    def hideCloseTip(self, event):
        self.close_all_tip.hide()

    # Call to reset all options to default, ideally only need to do this once
    def set_default_options(self):

        # Setup the base frame
        self['frameSize'] = (0, self.FRAME_WIDTH, 0, self.FRAME_HEIGHT)
        self['frameColor'] = self.FRAME_COLOR
        self['pos'] = self.OFFSCREEN_POS  # todo change to offscreen

        # Setup the image
        self.display_image(scale=self.IMAGE_SCALE, pos=self.IMAGE_POS)

        # Setup the text
        self['text_scale'] = self.TEXT_SCALE
        self['text_pos'] = self.TEXT_POS
        self['text_fg'] = self.TEXT_COLOR
        self['text_align'] = self.TEXT_ALIGN
        self.setText('Your SOMETHING now\ndoes SOMETHING COOL!\n\nFrom: SOME PLAYER')

    # Updates the image shown on the left side
    def display_image(self, scale, pos, path=None):
        if path is None:
            path = self.DEFAULT_IMAGE_PATH
        self.setImage(path)
        self.setTransparency(TransparencyAttrib.MAlpha)
        self['image_scale'] = scale
        self['image_pos'] = pos

    # Updates the text shown on the right-ish
    def update_text(self, text: str):
        self.setText(text)

    # Given a reward, update the elements immediately to represent this reward
    def display_reward(self, reward: APRewardGift):
        self._update_extra_items_remaining()
        self.setText(reward.get_display_string())
        self.display_image(reward.get_image_scale(), reward.get_image_pos(), reward.get_image_path())
        self._do_slide_sequence()

    # Escalate the count tier when items are received in quick succession, resetting back
    # to the normal sound once COMBO_WINDOW seconds pass without receiving an item
    def _update_count_tier(self):
        now = ClockObject.getGlobalClock().getRealTime()
        if self._last_item_time is None or (now - self._last_item_time) > self.COMBO_WINDOW:
            self._count_tier = 0
            self._wondrous_pitch = self.WONDROUS_PITCH_START
        else:
            self._count_tier = min(self._count_tier + 1, len(self.ITEM_SOUND_PATHS) - 1)
        self._last_item_time = now

    # Play the item pickup sound at the current escalation tier. Lower tiers use a random pitch;
    # the top (wondrous) tier climbs higher with each successive item, looping back to normal
    # pitch before it gets uncomfortably high
    def _play_item_sound(self):
        sound = self.item_sounds[self._count_tier]
        if sound is None:
            return
        if self._count_tier >= len(self.ITEM_SOUND_PATHS) - 1:
            pitch = self._wondrous_pitch
            self._wondrous_pitch += self.WONDROUS_PITCH_STEP
            if self._wondrous_pitch > self.WONDROUS_PITCH_MAX:
                self._wondrous_pitch = self.WONDROUS_PITCH_START
        else:
            pitch = random.choice(self.ITEM_SOUND_PITCHES)
        sound.setPlayRate(pitch)
        sound.play()

    # Update the progress bar that signals how long this reward is going to be present on screen
    def _set_bar_progress(self, amt):
        self.showtime_bar['value'] = amt

    # Update the amount of items waiting in queue to be displayed after this item
    def _update_extra_items_remaining(self):
        if len(self._reward_queue) < 1:
            self.additional_items_label['text'] = 'x'
            return

        self.additional_items_label['text'] = f"{str(len(self._reward_queue))}"

    # Perform a slide transition into view on screen to show the display
    def _do_slide_sequence(self):
        self._cleanup_intervals()

        self._slide_sequence = Sequence(
            Func(self.show),
            LerpPosInterval(self, startPos=self.OFFSCREEN_POS, pos=self.ONSCREEN_POS, duration=self.SLIDE_DURATION, blendType='easeInOut'),
            LerpFunctionInterval(self._set_bar_progress, duration=self.SHOW_DURATION, fromData=100, toData=0),
            LerpPosInterval(self, startPos=self.ONSCREEN_POS, pos=self.OFFSCREEN_POS, duration=self.SLIDE_DURATION, blendType='easeInOut'),
            Func(self.hide),
            Func(self._process_queue),
        )

        self._set_bar_progress(100)
        self._slide_sequence.start()

    # Perform a slide transition to immediately force this off screen
    def _do_hide_sequence(self):

        # If holding shift, we shift clicked the close button
        if self.__holding_shift:
            self._reward_queue.clear()

        self._cleanup_intervals()

        self._slide_sequence = Sequence(
            Func(self.show),
            LerpPosInterval(self, startPos=self.ONSCREEN_POS, pos=self.OFFSCREEN_POS, duration=self.SLIDE_DURATION, blendType='easeInOut'),
            Func(self.hide),
            Func(self._process_queue),
        )
        self._slide_sequence.start()

    # Called from elsewhere (LocalToon) to queue up a reward and display it if we already aren't
    def queue_reward(self, reward: APRewardGift):
        if not reward.shouldDisplay():
            return
        self._update_count_tier()
        self._play_item_sound()
        self._reward_queue.append(reward)
        self._update_extra_items_remaining()
        self._process_queue(fromSeq=False)

    # Called within this class to reshow the next item in queue or start the queue
    def _process_queue(self, fromSeq=True):

        # There is currently a sequence in progress, when it finishes it will call this anyway
        if not fromSeq and self._slide_sequence:
            return

        # There are no rewards to process, do nothing
        if len(self._reward_queue) <= 0:
            self._cleanup_intervals()
            return

        # No sequence playing, let's display the next item
        reward = self._reward_queue.pop(0)
        self.display_reward(reward)

    def _cleanup_intervals(self):
        if self._slide_sequence:
            self._slide_sequence.pause()
            self._slide_sequence = None

    def destroy(self):
        self.ignoreAll()
        self._cleanup_intervals()
        super().destroy()


# if __name__ == "__main__":
#     from direct.showbase.ShowBase import ShowBase
#
#     class MyApp(ShowBase):
#
#         def __init__(self):
#             ShowBase.__init__(self)
#
#             self.test_button = DirectButton(command=self.__give_reward)
#             self.test_button.reparentTo(self.aspect2d)
#
            # Test code goes here
            # self.ap_display = ArchipelagoRewardDisplay(
            #     frameColor=(0.1, 0.1, 0.1, .9),
            #     frameSize=(0, .9, 0, .2),
            #     pos=(0, 0, -.4),
            #     text='',
            #     text_scale=.035,
            #     text_align=TextNode.ACenter,
            #     text_fg=(1, 1, 1, 1),
            #     text_pos=(.55, 0.138)
            # )
            # self.ap_display.reparentTo(self.a2dTopLeft)
            # self.ap_display.set_default_options()
#
#         def __give_reward(self):
#             self.ap_display.queue_reward(APRewardGift(None, 'test player', False))
#
#
#     app = MyApp()
#     app.run()
