"""Read only local-player telemetry for the verified SSJJ x64 Mono runtime.

No remote execution or writes. Runtime layout is tied to the DLL digest;
managed fields are located by name, including inherited fields.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

MONO_SHA256 = '7cf37b3415c2a168c724163be78109323ca6f75112eb549f2197a528eaebb4c9'


@dataclass(frozen=True)
class PlayerDetails:
    hp: float | None
    max_hp: float | None
    stamina: float | None
    velocity_2d: int | None
    moving: bool | None
    view_pitch: float | None
    move_yaw: float | None
    duck_state: int | None
    current_weapon: int | None
    can_relive_time: int | None
    is_frozen: bool | None


@dataclass(frozen=True)
class PlayerState:
    map_id: int
    position: tuple[float, float, float]
    yaw: float
    dead: bool
    on_ground: bool
    client_time: int
    details: PlayerDetails | None = None


class SSJJReader:
    def __init__(self):
        import psutil
        from ctypes import wintypes

        self.handle = None
        matches = [p for p in psutil.process_iter(['name'])
                   if (p.info['name'] or '').lower() == 'ssjj_battleclient_unity.exe']
        if len(matches) != 1:
            raise RuntimeError('需要且只能运行一个 SSJJ_BattleClient_Unity.exe')
        process = matches[0]
        self.pid = process.pid
        dll = Path(process.exe()).parent / 'MonoBleedingEdge/EmbedRuntime/mono-2.0-bdwgc.dll'
        if hashlib.sha256(dll.read_bytes()).hexdigest() != MONO_SHA256:
            raise RuntimeError('游戏 Mono 版本未核实，请先更新读取布局')
        bases = [int(m.addr, 16) for m in process.memory_maps(grouped=False)
                 if Path(m.path).name.lower() == dll.name.lower()]
        if not bases:
            raise RuntimeError('未找到已加载的 Mono 模块')
        self.base = min(bases)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.kernel.ReadProcessMemory.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.handle = self.kernel.OpenProcess(0x1010, False, self.pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._fields_cache = {}
        try:
            images = {}
            node = self.ptr(self.base + 0x499d38)
            seen = set()
            while node and node not in seen and len(seen) < 512:
                seen.add(node)
                image = self.ptr(self.ptr(node) + 0x60)
                images[self.string(self.ptr(image + 0x28))] = image
                node = self.ptr(node + 8)
            self.ui = self.find_class(images['Assembly-CSharp'], 'UiIEventCondition',
                'Assets.Sources.Modules.Ui.UiEventCondition')
            self.contexts = self.find_class(images['SSJJEntitas_Library'], 'Contexts', '')
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, address, size):
        if not 0x10000 <= address < 0x7fffffffffff or not 0 < size <= 8_000_000:
            raise RuntimeError('无效的游戏内存范围')
        buffer = ctypes.create_string_buffer(size)
        received = ctypes.c_size_t()
        if not self.kernel.ReadProcessMemory(self.handle, address, buffer, size,
                ctypes.byref(received)) or received.value != size:
            raise RuntimeError('游戏状态读取失败；游戏可能已退出或切换场景')
        return buffer.raw

    def ptr(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def u32(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def string(self, address):
        return self.read(address, 128).split(b'\0')[0].decode('utf-8', 'strict')

    def classof(self, obj):
        return self.ptr(self.ptr(obj))

    def classname(self, klass):
        return self.string(self.ptr(klass + 0x48))

    def find_class(self, image, name, namespace):
        cache = image + 0x4c0
        count = self.u32(cache + 0x18)
        if not 0 < count < 100000:
            raise RuntimeError('无效的 Mono 类缓存')
        buckets = struct.unpack('<' + 'Q' * count, self.read(self.ptr(cache + 0x20), count * 8))
        seen = set()
        for klass in buckets:
            while klass and klass not in seen:
                seen.add(klass)
                if len(seen) > 100000:
                    raise RuntimeError('Mono 类缓存过大')
                if self.classname(klass) == name and self.string(self.ptr(klass + 0x50)) == namespace:
                    return klass
                klass = self.ptr(klass + 0x108)
        raise RuntimeError(f'尚未加载游戏类：{namespace}.{name}，请进入关卡')

    def fields(self, klass):
        if klass not in self._fields_cache:
            result = {}
            parent = self.ptr(klass + 0x30)
            if parent:
                result.update(self.fields(parent))
            kind = (self.u32(klass + 0x28) >> 16) & 7
            definition = self.ptr(self.ptr(klass + 0xf0)) if kind == 3 else klass
            count = self.u32(definition + 0x100) if kind in (1, 2, 3) else 0
            if count > 10000:
                raise RuntimeError('无效的 Mono 字段数量')
            start = self.ptr(klass + 0x98)
            for index in range(count):
                field = start + index * 32
                result[self.string(self.ptr(field + 8))] = self.u32(field + 24)
            self._fields_cache[klass] = result
        return self._fields_cache[klass]

    def field(self, obj, name):
        return obj + self.fields(self.classof(obj))[name]

    def static(self, klass):
        root = self.ptr(self.base + 0x499c78)
        domain_id = self.u32(root + 0xbc)
        if domain_id > 255:
            raise RuntimeError('无效的 Mono 域')
        runtime = self.ptr(klass + 0xd0)
        vtable = self.ptr(runtime + 8 + domain_id * 8)
        if self.ptr(vtable) != klass:
            raise RuntimeError('Mono 静态字段布局不匹配')
        return self.ptr(vtable + 0x40 + 8 * self.u32(klass + 0x5c))

    def components(self, entity):
        array = self.ptr(self.field(entity, '_components'))
        count = self.ptr(array + 24)
        if count > 4096:
            raise RuntimeError('无效的组件数组')
        return [value for value in struct.unpack('<' + 'Q' * count,
            self.read(array + 32, count * 8)) if value] if count else []

    def map_id(self):
        contexts = self.ptr(self.static(self.contexts) + self.fields(self.contexts)['_sharedInstance'])
        room = self.ptr(self.field(contexts, '<battleRoom>k__BackingField'))
        entities = self.ptr(self.field(room, '_entities'))
        slots = self.ptr(self.field(entities, '_slots'))
        count = self.u32(self.field(entities, '_lastIndex'))
        if count > 4096 or count > self.ptr(slots + 24):
            raise RuntimeError('无效的房间实体集合')
        for index in range(count):
            slot = slots + 32 + index * 16
            if struct.unpack('<i', self.read(slot, 4))[0] < 0:
                continue
            entity = self.ptr(slot + 8)
            if not entity:
                continue
            for component in self.components(entity):
                if self.classname(self.classof(component)) == 'MapInfoComponent':
                    info = self.ptr(self.field(component, 'MapInfo'))
                    return self.u32(self.field(info, '_id'))
        raise RuntimeError('当前未进入地图')

    def nullable(self, obj, name, fmt, *, required=True):
        address = self.field(obj, name)
        size = struct.calcsize(fmt)
        data = self.read(address, size + 1)
        if not required and data[size] == 0:
            return None
        if data[size] != 1:
            raise RuntimeError(f'角色字段尚无有效值：{name}')
        value = struct.unpack(fmt, data[:size])[0]
        if not required and isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    def read_details(self, current):
        return PlayerDetails(
            hp=self.nullable(current, '_hp', '<f', required=False),
            max_hp=self.nullable(current, '_maxHp', '<f', required=False),
            stamina=self.nullable(current, '_stamina', '<f', required=False),
            velocity_2d=self.nullable(current, '_velocity2D', '<i', required=False),
            moving=self.nullable(current, '_moving', '<?', required=False),
            view_pitch=self.nullable(current, '_viewPitch', '<f', required=False),
            move_yaw=self.nullable(current, '_moveYaw', '<f', required=False),
            duck_state=self.nullable(current, '_duckState', '<i', required=False),
            current_weapon=self.nullable(current, '_currentWeapon', '<i', required=False),
            can_relive_time=self.nullable(current, '_canReliveTime', '<i', required=False),
            is_frozen=self.nullable(current, '_isFreeze', '<?', required=False),
        )

    def snapshot(self, *, include_details=False):
        player = self.ptr(self.static(self.ui) + self.fields(self.ui)['MyPlayerEntity'])
        for component in self.components(player):
            if self.classname(self.classof(component)) == 'BasicInfoComponent':
                current = self.ptr(self.field(component, 'Current'))
                position = tuple(self.nullable(current, axis, '<f') for axis in ('_x', '_y', '_z'))
                yaw = self.nullable(current, '_viewYaw', '<f')
                if not all(math.isfinite(v) and abs(v) < 1e8 for v in (*position, yaw)):
                    raise RuntimeError('角色坐标或朝向无效')
                return PlayerState(self.map_id(), position, yaw,
                    self.nullable(current, '_isDead', '<?'),
                    self.nullable(current, '_onGround', '<?'),
                    self.nullable(current, '_clientTime', '<i'),
                    self.read_details(current) if include_details else None)
        raise RuntimeError('本人角色状态尚未就绪')

    def require_foreground(self):
        from ctypes import wintypes
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(self.user.GetForegroundWindow(), ctypes.byref(pid))
        if pid.value != self.pid:
            raise RuntimeError('游戏必须在前台，已停止跑图')


if __name__ == '__main__':
    with SSJJReader() as reader:
        print(json.dumps(asdict(reader.snapshot()), ensure_ascii=False))
