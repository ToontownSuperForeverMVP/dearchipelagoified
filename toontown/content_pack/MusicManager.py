from toontown.toonbase import ToontownGlobals
from panda3d.core import DecalEffect, VirtualFileSystem, Filename
from direct.interval.IntervalGlobal import *
import json
import random
import os
import re
import subprocess
import threading
import shutil
import ctypes
import math
import struct
import concurrent.futures

class MusicManager:

    def __init__(self):
        fileSystem = VirtualFileSystem.getGlobalPtr()
        if not fileSystem.exists(ToontownGlobals.musicJsonFilePath):
            mount_path = Filename.fromOsSpecific(os.path.abspath('resources'))
            fileSystem.mount(mount_path, '/', VirtualFileSystem.MFReadOnly)
        if fileSystem.exists(ToontownGlobals.musicJsonFilePath):
            self.musicJson = json.loads(fileSystem.readFile(ToontownGlobals.musicJsonFilePath, True))
        elif os.path.exists("resources/content_pack/music.json"):
            with open("resources/content_pack/music.json", "r", encoding="utf-8") as f:
                self.musicJson = json.load(f)
        else:
            self.musicJson = {"global_music": {}}
        self.musicJsonCopy = json.loads(json.dumps(self.musicJson))
        self.previousMusic = None
        self.currentMusic = {}
        self.currentMusicInfo = {}
        self.randomMusicInfo = {}
        self.storedMusicInfo = {}
        self.lastPlayedTrack = {}
        self.shuffledMusic = {}
        self.sadxTrackMetadata = {}
        self.sadxCategorizedMusic = {'adventurefield': [], 'level': [], 'boss': [], 'theme': [], 'jingle': [], 'any': []}
        self.adxCacheDir = "resources/music_packs/.cache"
        self._adxLock = threading.Lock()
        self._precacheThread = None
        self._adxDecoderFunc = None
        self._initAdxDecoder()
        self._loadMusicPacks()
        self._startAdxPrecache()

    def _initAdxDecoder(self):
        try:
            dll_dir = os.path.dirname(os.path.abspath(__file__))
            dll_path = os.path.join(dll_dir, 'adx_decoder.dll')
            if not os.path.exists(dll_path):
                dll_path = os.path.abspath('toontown/content_pack/adx_decoder.dll')
            if os.path.exists(dll_path):
                dll = ctypes.CDLL(dll_path)
                func = dll.decode_adx_to_pcm
                func.argtypes = [
                    ctypes.c_char_p,
                    ctypes.c_ulonglong,
                    ctypes.c_ulonglong,
                    ctypes.c_uint,
                    ctypes.c_uint,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_void_p,
                    ctypes.c_ulonglong
                ]
                func.restype = ctypes.c_int
                self._adxDecoderFunc = func
        except Exception:
            self._adxDecoderFunc = None
    
    def _parseJsonLenient(self, text):
        text = text.strip().lstrip('\ufeff')
        try:
            return json.loads(text)
        except Exception:
            pass
        if not text.startswith('{'):
            wrapped = '{' + text.rstrip(',\r\n\t ') + '}'
            try:
                return json.loads(wrapped)
            except Exception:
                pass
        cleaned = re.sub(r',\s*([\}\]])', r'\1', text)
        if not cleaned.startswith('{'):
            cleaned = '{' + cleaned.rstrip(',\r\n\t ') + '}'
        try:
            return json.loads(cleaned)
        except Exception:
            return {}

    def _classifyToontownKey(self, key):
        k = key.lower()
        if 'battle' in k or ('boss' in k and not any(x in k for x in ('victory', 'dance', 'defeated', 'elevator', 'lobby'))):
            return 'boss'
        if any(x in k for x in ('defeated', 'sting', 'jingle')):
            return 'jingle'
        if any(x in k for x in ('theme', 'victory', 'dance', 'promotion', 'epilogue', 'firework', 'create-a-toon', 'none')):
            return 'theme'
        if any(x in k for x in ('-sz', 'toon-hall', 'gag-shop', '-activity')):
            return 'adventurefield'
        return 'level'

    def _convertAdxToWav(self, src_path, dst_path):
        with self._adxLock:
            if os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
                try:
                    if os.path.getmtime(dst_path) >= os.path.getmtime(src_path):
                        return dst_path
                except Exception:
                    return dst_path
            parent_dir = os.path.dirname(dst_path)
            if parent_dir and not os.path.exists(parent_dir):
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except Exception:
                    pass
            if getattr(self, '_adxDecoderFunc', None) is not None:
                try:
                    with open(src_path, 'rb') as f:
                        data = f.read()
                    if len(data) >= 24 and data[0] == 0x80 and data[1] == 0x00:
                        cp_off = struct.unpack('>H', data[2:4])[0]
                        channels = data[7]
                        rate = struct.unpack('>I', data[8:12])[0]
                        samples = struct.unpack('>I', data[12:16])[0]
                        hp = struct.unpack('>H', data[16:18])[0]
                        if channels in (1, 2) and rate > 0 and samples > 0:
                            a = math.sqrt(2.0) - math.cos(2.0 * math.pi * hp / rate)
                            b = math.sqrt(2.0) - 1.0
                            c = (a - math.sqrt((a + b) * (a - b))) / b
                            c1 = int(math.floor(c * 8192.0))
                            c2 = int(math.floor(c * c * -4096.0))
                            audio_start = cp_off + 4
                            pcm_buf = (ctypes.c_short * (samples * channels))()
                            written = self._adxDecoderFunc(
                                data,
                                len(data),
                                audio_start,
                                samples,
                                channels,
                                c1,
                                c2,
                                ctypes.cast(pcm_buf, ctypes.c_void_p),
                                samples
                            )
                            if written > 0:
                                data_size = written * channels * 2
                                wav_hdr = struct.pack(
                                    '<4sI4s4sIHHIIHH4sI',
                                    b'RIFF', 36 + data_size, b'WAVE',
                                    b'fmt ', 16, 1, channels, rate, rate * channels * 2, channels * 2, 16,
                                    b'data', data_size
                                )
                                with open(dst_path, 'wb') as f:
                                    f.write(wav_hdr)
                                    f.write(pcm_buf)
                                if os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
                                    return dst_path
                except Exception:
                    pass
            ffmpeg_path = os.path.abspath('Panda3D/bin/ffmpeg.exe')
            if not os.path.exists(ffmpeg_path):
                ffmpeg_path = shutil.which('ffmpeg') or 'ffmpeg'
            try:
                subprocess.run([ffmpeg_path, '-threads', '0', '-y', '-i', os.path.abspath(src_path), '-f', 'wav', os.path.abspath(dst_path)], capture_output=True)
            except Exception:
                pass
            if os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
                return dst_path
            return None

    def _getPlayablePath(self, track_path):
        if not track_path or not track_path.lower().endswith('.adx'):
            return track_path
        norm_path = track_path.replace('\\', '/')
        if not os.path.exists(norm_path) and os.path.exists(track_path):
            norm_path = track_path
        if not os.path.exists(norm_path):
            return track_path
        try:
            rel = os.path.relpath(norm_path, 'resources/music_packs').replace('\\', '/')
        except Exception:
            rel = os.path.basename(norm_path)
        safe_name = rel.replace('/', '_').replace('\\', '_').replace('..', '_')
        if not safe_name.lower().endswith('.wav'):
            safe_name = os.path.splitext(safe_name)[0] + '.wav'
        cache_path = os.path.join(self.adxCacheDir, safe_name).replace('\\', '/')
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                if os.path.getmtime(cache_path) >= os.path.getmtime(norm_path):
                    return cache_path
            except Exception:
                return cache_path
        converted = self._convertAdxToWav(norm_path, cache_path)
        if converted and os.path.exists(converted) and os.path.getsize(converted) > 0:
            return converted
        return norm_path

    def _startAdxPrecache(self):
        try:
            self._precacheThread = threading.Thread(target=self._precacheAdxWorker, daemon=True)
            self._precacheThread.start()
        except Exception:
            pass

    def _precacheAdxWorker(self):
        packs_dir = "resources/music_packs"
        if not os.path.exists(packs_dir):
            return
        adx_files = []
        try:
            for root, folders, files in os.walk(packs_dir):
                if '.cache' in root.replace('\\', '/').split('/'):
                    continue
                for f in files:
                    if f.lower().endswith('.adx'):
                        src = os.path.join(root, f).replace('\\', '/')
                        adx_files.append(src)
        except Exception:
            return
        if not adx_files:
            return
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(self._getPlayablePath, adx_files))
        except Exception:
            for src in adx_files:
                self._getPlayablePath(src)
        except Exception:
            pass

    def playMusic(self, json_code, looping=True, volume=1.0, interrupt=True, time=0.0, refresh=False, randomToggle=False):
        import builtins
        base = getattr(builtins, 'base', None)
        if self.previousMusic == json_code and not randomToggle:
            return
        possible_paths = self.musicJson.get('global_music', {}).get(json_code, [])
        # Don't stop when music paths being called are the same AND rando isn't on
        # Checking for self.currentMusic will catch cases where the music is force stopped anyway (entering tunnels), to just start the track anyways
        if self.storedMusicInfo and self.currentMusic:
            if self.storedMusicInfo.get(list(self.storedMusicInfo.keys())[0]).get("path", []) == possible_paths and not base.randomMusic:
                return
        self.storedMusicInfo = {
            json_code: {"looping": looping, "volume": volume, "interrupt": interrupt, "time": time, "path": possible_paths}
        }
        if self.currentMusic and interrupt:
            self.stopMusic()
        self.previousMusic = json_code
        if getattr(base, 'randomMusic', False):
            is_wild = False
            if getattr(base, 'localAvatar', None) and base.localAvatar.hasConnected():
                rng_option = base.localAvatar.slotData.get('seed_generation_type', 0)
                if rng_option in (3, 'wild'):
                    is_wild = True
            if is_wild:
                pool = self._getTrackPool()
                allowed_tracks = [t for t in pool if t != self.lastPlayedTrack.get(json_code)]
                if not allowed_tracks:
                    allowed_tracks = pool
                json_code_path = random.choice(allowed_tracks)
                self.lastPlayedTrack[json_code] = json_code_path
            else:
                if not getattr(self, 'shuffledMusic', {}):
                    self.setRandomizedMusic()
                if json_code in self.shuffledMusic:
                    json_code_path = self.shuffledMusic[json_code]
                else:
                    pool = self._getTrackPool()
                    if pool:
                        json_code_path = random.choice(pool)
                    elif possible_paths:
                        json_code_path = random.choice(possible_paths)
                    else:
                        return
        else:
            if possible_paths:
                json_code_path = random.choice(possible_paths)
            else:
                return
        meta_type = self._getTrackType(json_code_path)
        if meta_type == 'jingle':
            looping = False
        playable_path = self._getPlayablePath(json_code_path)
        self.currentMusic[json_code] = base.loader.loadMusic(playable_path)
        self.currentMusic[json_code].setLoop(looping)
        self.currentMusic[json_code].setVolume(volume)
        self.currentMusic[json_code].setTime(time)
        self.currentMusicInfo[json_code] = {"looping": looping, "volume": volume, "interrupt": interrupt, "time": time, "path": [json_code_path]}
        base.playMusic(self.currentMusic[json_code], looping=looping, interrupt=interrupt, volume=volume, time=time)
        if getattr(base, 'randomMusic', False):
            track_name = self._getTrackName(json_code_path)
            if getattr(base, 'localAvatar', None):
                from libotp.nametag.WhisperGlobals import WhisperType
                base.localAvatar.setSystemMessage(0, "Now Playing: " + track_name, whisperType=WhisperType.WTEmote)

    def _loadMusicPacks(self):
        packs_dir = "resources/music_packs"
        if not os.path.exists(packs_dir):
            try:
                os.makedirs(packs_dir)
            except Exception:
                pass
            return
        if not os.path.exists(self.adxCacheDir):
            try:
                os.makedirs(self.adxCacheDir, exist_ok=True)
            except Exception:
                pass
        all_audio_files = []
        def mergePackEntries(key, entries):
            if not isinstance(entries, list):
                return
            global_music = self.musicJson.get("global_music", {})
            if key not in global_music:
                return
            original_entries = self.musicJsonCopy.get("global_music", {}).get(key, [])
            if global_music[key] == original_entries:
                global_music[key] = list(entries)
                return
            global_music[key].extend(entry for entry in entries if entry not in global_music[key])

        try:
            for root, folders, files in os.walk(packs_dir):
                if '.cache' in root.replace('\\', '/').split('/'):
                    continue
                for f in files:
                    if f.lower().endswith((".ogg", ".mp3", ".wav", ".flac", ".wma", ".aac", ".m4a", ".opus", ".adx")):
                        all_audio_files.append(os.path.join(root, f).replace("\\", "/"))
        except Exception:
            pass

        audio_lookup = {}
        for af in all_audio_files:
            af_norm = af.replace('\\', '/')
            base_no_ext = os.path.splitext(os.path.basename(af_norm))[0].lower()
            rel_no_ext = os.path.splitext(os.path.relpath(af_norm, packs_dir).replace('\\', '/'))[0].lower()
            audio_lookup[af_norm.lower()] = af_norm
            audio_lookup[rel_no_ext] = af_norm
            audio_lookup[base_no_ext] = af_norm

        try:
            for root, folders, files in os.walk(packs_dir):
                if '.cache' in root.replace('\\', '/').split('/'):
                    continue
                for f in files:
                    if f.lower().endswith(".json"):
                        json_file_path = os.path.join(root, f)
                        try:
                            with open(json_file_path, 'r', encoding='utf-8', errors='ignore') as jf:
                                content = jf.read()
                        except Exception:
                            continue
                        pack_data = self._parseJsonLenient(content)
                        if not isinstance(pack_data, dict):
                            continue
                        if "global_music" in pack_data and isinstance(pack_data["global_music"], dict):
                            for key, val in pack_data["global_music"].items():
                                mergePackEntries(key, val)
                            continue
                        is_ttap_format = False
                        for key, val in pack_data.items():
                            if key in self.musicJson.get("global_music", {}) and isinstance(val, list):
                                mergePackEntries(key, val)
                                is_ttap_format = True
                        if is_ttap_format:
                            continue
                        for key, val in pack_data.items():
                            if isinstance(val, dict):
                                track_name = val.get("name", "")
                                track_type = str(val.get("type", "any")).lower()
                            else:
                                track_name = str(val)
                                track_type = "any"
                            norm_k = key.replace('\\', '/').strip('/')
                            norm_k_no_ext = os.path.splitext(norm_k)[0]
                            base_k_no_ext = os.path.splitext(os.path.basename(norm_k))[0]
                            matched_audio = audio_lookup.get(norm_k.lower()) or audio_lookup.get(norm_k_no_ext.lower()) or audio_lookup.get(base_k_no_ext.lower())
                            meta_entry = {"name": track_name or base_k_no_ext, "type": track_type, "path": matched_audio or norm_k}
                            self.sadxTrackMetadata[key] = meta_entry
                            self.sadxTrackMetadata[norm_k] = meta_entry
                            self.sadxTrackMetadata[norm_k_no_ext] = meta_entry
                            self.sadxTrackMetadata[base_k_no_ext] = meta_entry
                            if matched_audio:
                                self.sadxTrackMetadata[matched_audio] = meta_entry
                                self.sadxTrackMetadata[matched_audio.lower()] = meta_entry
                                if track_type in self.sadxCategorizedMusic:
                                    if matched_audio not in self.sadxCategorizedMusic[track_type]:
                                        self.sadxCategorizedMusic[track_type].append(matched_audio)
                                else:
                                    if matched_audio not in self.sadxCategorizedMusic['any']:
                                        self.sadxCategorizedMusic['any'].append(matched_audio)
            
            any_tracks = self.sadxCategorizedMusic.get('any', [])
            for key in list(self.musicJson.get("global_music", {}).keys()):
                category = self._classifyToontownKey(key)
                matching = self.sadxCategorizedMusic.get(category, []) + any_tracks
                if matching:
                    existing = self.musicJson.get("global_music", {}).get(key, [])
                    merged = list(existing)
                    for m in matching:
                        if m not in merged:
                            merged.append(m)
                    self.musicJson.get("global_music", {})[key] = merged
        except Exception as e:
            print(e)

    def _getTrackPool(self):
        import builtins
        base = getattr(builtins, 'base', None)
        in_game_paths = []
        for paths in self.musicJson.get('global_music', {}).values():
            for p in paths:
                if p not in in_game_paths:
                    in_game_paths.append(p)
        custom_paths = []
        custom_dir = "resources/music_packs"
        if os.path.exists(custom_dir):
            try:
                for root, folders, files in os.walk(custom_dir):
                    if '.cache' in root.replace('\\', '/').split('/'):
                        continue
                    for f in files:
                        if f.lower().endswith((".ogg", ".mp3", ".wav", ".flac", ".wma", ".aac", ".m4a", ".opus", ".adx")):
                            custom_paths.append(os.path.join(root, f).replace("\\", "/"))
            except Exception:
                pass
        style = getattr(base, 'randomMusicStyle', 'Mix')
        if style == 'Custom Only':
            if custom_paths:
                return custom_paths
            return in_game_paths
        elif style == 'Mix':
            return custom_paths + in_game_paths
        else:
            return in_game_paths

    def _getTrackType(self, track_path):
        if not track_path:
            return "any"
        norm_path = track_path.replace('\\', '/')
        base_no_ext = os.path.splitext(os.path.basename(norm_path))[0]
        try:
            rel_no_ext = os.path.splitext(os.path.relpath(norm_path, 'resources/music_packs').replace('\\', '/'))[0]
        except Exception:
            rel_no_ext = base_no_ext
        for candidate in (track_path, norm_path, norm_path.lower(), rel_no_ext, rel_no_ext.lower(), base_no_ext, base_no_ext.lower()):
            if candidate in self.sadxTrackMetadata:
                return self.sadxTrackMetadata[candidate].get("type", "any")
        return "any"

    def _getTrackName(self, track_path):
        if not track_path:
            return "Unknown Track"
        norm_path = track_path.replace('\\', '/')
        base_name = os.path.basename(norm_path)
        base_no_ext = os.path.splitext(base_name)[0]
        try:
            rel_no_ext = os.path.splitext(os.path.relpath(norm_path, 'resources/music_packs').replace('\\', '/'))[0]
        except Exception:
            rel_no_ext = base_no_ext
        for candidate in (track_path, norm_path, norm_path.lower(), rel_no_ext, rel_no_ext.lower(), base_no_ext, base_no_ext.lower()):
            if candidate in self.sadxTrackMetadata and self.sadxTrackMetadata[candidate].get("name"):
                return self.sadxTrackMetadata[candidate]["name"]
        title = self._getAudioTitle(track_path)
        if title:
            return title
        return base_no_ext.replace('_', ' ').replace('-', ' ').strip().title()

    def _getAudioTitle(self, file_path):
        import os
        ext = os.path.splitext(file_path)[1].lower()
        if ext in ('.ogg', '.flac'):
            return self._getOggTitle(file_path)
        elif ext == '.mp3':
            return self._getMp3Title(file_path)
        return None

    def _getMp3Title(self, file_path):
        try:
            with open(file_path, 'rb') as f:
                data = f.read(16384)
            idx = data.find(b'TIT2')
            if idx != -1:
                if len(data) >= idx + 11:
                    size_bytes = data[idx+4:idx+8]
                    frame_size = int.from_bytes(size_bytes, byteorder='big')
                    if 0 < frame_size < 1024:
                        encoding = data[idx+10]
                        raw_bytes = data[idx+11 : idx+11+frame_size-1]
                        if encoding == 0:
                            title = raw_bytes.decode('iso-8859-1', errors='ignore')
                        elif encoding == 1:
                            title = raw_bytes.decode('utf-16', errors='ignore')
                        elif encoding == 2:
                            title = raw_bytes.decode('utf-16-be', errors='ignore')
                        elif encoding == 3:
                            title = raw_bytes.decode('utf-8', errors='ignore')
                        else:
                            title = raw_bytes.decode('utf-8', errors='ignore')
                        title = title.replace('\x00', '').strip()
                        if title:
                            return title
            with open(file_path, 'rb') as f:
                f.seek(0, 2)
                file_size = f.tell()
                if file_size >= 128:
                    f.seek(-128, 2)
                    tag = f.read(3)
                    if tag == b'TAG':
                        title_bytes = f.read(30)
                        title = title_bytes.decode('iso-8859-1', errors='ignore').replace('\x00', '').strip()
                        if title:
                            return title
        except Exception:
            pass
        return None

    def _getOggTitle(self, file_path):
        try:
            with open(file_path, 'rb') as f:
                data = f.read(16384)
            idx = data.lower().find(b'title=')
            if idx != -1:
                start = idx + 6
                title_bytes = bytearray()
                for i in range(start, min(start + 128, len(data))):
                    b = data[i]
                    if 32 <= b <= 126 or b >= 128:
                        title_bytes.append(b)
                    else:
                        break
                title_str = title_bytes.decode('utf-8', errors='ignore').strip()
                if title_str:
                    return title_str
        except Exception:
            pass
        return None

    # Used when we are disabling music randomizer
    def getNormalMusicInfo(self):
        if self.storedMusicInfo:
            return self.storedMusicInfo
        return None

    def setRandomizedMusic(self):
        import builtins
        base = getattr(builtins, 'base', None)
        self.lastPlayedTrack = {}
        self.shuffledMusic = {}
        is_wild = False
        seed = None
        if getattr(base, 'localAvatar', None) and base.localAvatar.hasConnected():
            rng_option = base.localAvatar.slotData.get('seed_generation_type', 0)
            if rng_option in (3, 'wild'):
                is_wild = True
            else:
                seed = base.localAvatar.getSeed()
        if is_wild:
            return
        pool = self._getTrackPool()
        if not pool:
            return
        rng = random.Random()
        if seed is not None:
            rng.seed(seed)
        keys = sorted(list(self.musicJson.get('global_music', {}).keys()))
        pool_copy = sorted(list(pool))
        rng.shuffle(pool_copy)
        for i, key in enumerate(keys):
            self.shuffledMusic[key] = pool_copy[i % len(pool_copy)]

    def stopMusic(self):        
        for music in list(self.currentMusic.keys()):
            self.currentMusic[music].stop()
        self.currentMusic = {}
        self.currentMusicInfo = {}

    def stopSpecificMusic(self, json_code):
        if json_code in list(self.currentMusic.keys()):
            self.currentMusic[json_code].stop()
            # using pop just incase we try to stop a specific music that isn't in the dict
            self.currentMusic.pop(json_code, None)
            self.currentMusicInfo.pop(json_code, None)

    def setVolume(self, volume=1.0):
        for music in list(self.currentMusic.keys()):
            self.currentMusic[music].setVolume(volume)
            self.currentMusicInfo[music]["volume"] = volume

    def setSpecificVolume(self, json_code, volume=1.0):
        if json_code in list(self.currentMusic.keys()):
            self.currentMusic[json_code].setVolume(volume)
            self.currentMusicInfo[json_code]["volume"] = volume

    def getTime(self, json_code):
        # We need to update the current time for this music
        self.updateTime(json_code)
        return self.currentMusicInfo[json_code]["time"]

    def updateTime(self, json_code):
        self.currentMusicInfo[json_code]["time"] = self.currentMusic[json_code].getTime()
        
    def getCurMusic(self):
        return self.currentMusic
    
    def getCurMusicInfo(self):
        # We need to update the current times for all music
        for code in list(self.currentMusicInfo.keys()):
            self.updateTime(code)
        return self.currentMusicInfo
    
    def setLoop(self, value):
        """
        Set the looping value for all current music tracks.
        
        :param value: The value to set.
        """
        for music in list(self.currentMusic.keys()):
            self.currentMusic[music].setLoop(value)
            self.currentMusicInfo[music]["looping"] = value
    
    def setSpecificLoop(self, json_code, value):
        """
        Set the looping value for a specific music track.
        
        :param json_code: The json code for the music track.
        :param value: The value to set.
        """
        if json_code in list(self.currentMusic.keys()):
            self.currentMusic[json_code].setLoop(value)
            self.currentMusicInfo[json_code]["looping"] = value            
            
    def getVolume(self):
        """
        Retrieve the volume of the current music track.
        """
        return self.currentMusic[list(self.currentMusic.keys())[0]].getVolume()
    
    def getSpecifcVolume(self, json_code):
        """
        Get the volume of a specific music track.
        
        :param json_code: The json code for the music track.
        """
        if json_code in list(self.currentMusic.keys()):
            return self.currentMusic[json_code].getVolume()
        return None
    
    def getSpecifcPlayRate(self, json_code):
        """
        Get the play rate of a specific music track.
        
        :param json_code: The json code for the music track.
        """
        if json_code in list(self.currentMusic.keys()):
            return self.currentMusic[json_code].getPlayRate()
        return None
            
    def setSpecifcPlayRate(self, json_code, rate):
        """
        Set the play rate of a specific music track.
        """
        if json_code in list(self.currentMusic.keys()):
            self.currentMusic[json_code].setPlayRate(rate)
            self.currentMusicInfo[json_code]["rate"] = rate
    
    def setPlayRate(self, rate):
        """
        Set the play rate of the current music.
        """
        for music in list(self.currentMusic.keys()):
            self.currentMusic[music].setPlayRate(rate)
            self.currentMusicInfo[music]["rate"] = rate

    def lerpPlayRate(self, json_key, to_data, duration):
        self.currentMusicInfo[json_key]["rate"] = to_data
        return LerpFunctionInterval(self.currentMusic[json_key].setPlayRate, fromData=self.getSpecifcPlayRate(json_key), toData=to_data, duration=duration)

    def lerpVolume(self, json_key, to_data, duration):
        self.currentMusicInfo[json_key]["volume"] = to_data
        return LerpFunctionInterval(self.currentMusic[json_key].setVolume, fromData=self.getVolume(), toData=to_data, duration=duration)
