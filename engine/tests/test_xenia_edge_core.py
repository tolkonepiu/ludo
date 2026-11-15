"""Xenia's native/core route selection, formats, and per-launch configuration."""
import shlex
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from romm_sync_engine import sync_core as core

COUNT = 0
FAILED = []


def check(name, got, want):
    global COUNT
    COUNT += 1
    ok = got == want
    print('ok  ' if ok else 'FAIL', name, '' if ok else f'{got!r} != {want!r}')
    if not ok:
        FAILED.append(name)


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'synthetic')
    return path


def interface(root, native, installed, pinned=False, retroarch=True):
    ra = core.RetroArchInterface.__new__(core.RetroArchInterface)
    ra.settings = SimpleNamespace(get=lambda section, key, default='':
        ('xenia_edge' if pinned else '') if section == 'CoreOverrides' else default)
    ra.retroarch_executable = '/usr/bin/retroarch' if retroarch else ''
    ra.platform_core_map = {}
    ra.get_available_cores = lambda: {'xenia_edge': str(root / 'cores/xenia_edge_libretro.so')} if installed else {}
    ra._retrodeck_core_map = lambda: {}
    ra.core_download_support = lambda: {'available': True}
    ra.list_downloadable_cores = lambda: {'xenia_edge': {'file': 'xenia_edge_libretro.so.zip'}}
    ra.find_retroarch_config_dir = lambda: str(root / 'config')
    ra.get_retroarch_config_setting = lambda key, default='': default
    ra._menu_toggle_combo = lambda: ''
    ra._gamescope_running = lambda: False
    ra.sort_calls = []
    ra.ensure_content_save_sorting = lambda: ra.sort_calls.append(True)
    ra._ensure_per_game_vmu = lambda *args: None
    ra._ensure_ps2_bios = lambda *args: None
    ra._ensure_ps2_memcards = lambda *args: None
    return ra


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        iso = touch(root / 'Game.iso')
        cfg = touch(root / 'config/retroarch.cfg')
        cfg.write_text('video_driver = "gl"\nsort_savefiles_enable = "false"\n')
        original = cfg.read_bytes()
        for label, native, installed, pin, retroarch, route, resolved, source in (
                ('native only', True, False, False, False, '', 'Xenia', 'standalone'),
                ('core only', False, True, False, True, 'xenia_edge', 'xenia_edge', 'guess'),
                ('both auto', True, True, False, True, '', 'Xenia', 'standalone'),
                ('both pinned', True, True, True, True, 'xenia_edge', 'xenia_edge', 'override'),
                ('pinned missing', True, False, True, True, 'xenia_edge', None, 'none'),
                ('neither', False, False, False, False, 'xenia_edge', None, 'none')):
            ra = interface(root, native, installed, pin, retroarch)
            with patch.object(core, 'find_standalone_executable', return_value='/native/xenia_edge' if native else ''):
                status = ra.standalone_emulator_status('Custom label', 'xbox360')
                check(label + ' native availability unchanged', status['installed'], native)
                check(label + ' route', status['launch_core'], route)
                check(label + ' status list route', ra.standalone_emulators_status()[0]['launch_core'], route)
                description = ra.describe_core_resolution('Custom label', 'xbox360')
                check(label + ' resolution', (description['resolved_core'], description['source']), (resolved, source))
                check(label + ' override retained', description['override'], 'xenia_edge' if pin else '')
                cmd, error = ra.build_launch_command(iso, 'Custom label', entry_slot=2, platform_slug='xbox360')
                check(label + ' no save sorting mutation', ra.sort_calls, [])
                if resolved:
                    check(label + ' launch succeeds', error, None)
                    check(label + ' executable', cmd[0], '/native/xenia_edge' if not route else '/usr/bin/retroarch')
                    check(label + ' no state slot', '--entryslot' in cmd, False)
                    check(label + ' no standalone fullscreen flag', '--fullscreen=true' in cmd, False)
                    if route:
                        check(label + ' core argv', cmd[1:4], ['-L', str(root / 'cores/xenia_edge_libretro.so'), str(iso)])
                        overlay = Path(cmd[cmd.index('--appendconfig') + 1]).read_text()
                        check(label + ' Vulkan overlay', 'video_driver = "vulkan"' in overlay, True)
                    steam = core.SteamShortcutManager.__new__(core.SteamShortcutManager)
                    steam.retroarch, steam.log = ra, lambda message: None
                    exe, options = steam._build_launch_command(iso, 'Custom label', 'xbox360')
                    check(label + ' Steam parity', shlex.split(exe) + shlex.split(options), cmd)
                    focus = []
                    ra._host_subprocess_env = lambda: {}
                    ra._focus_window_after_launch = lambda proc, match: focus.append(match)
                    process = SimpleNamespace(poll=lambda: None)
                    with patch('subprocess.Popen', return_value=process), \
                            patch.object(core.time, 'sleep'), patch.object(core.threading, 'Thread'):
                        success, message = ra.launch_game(iso, 'Custom label', platform_slug='xbox360')
                    check(label + ' launched with route focus', (success, focus), (True, ['retroarch' if route else 'xenia']))
                    check(label + ' success names actual route',
                          ('with ' in message) if route else ('in Xenia' in message), True)
                else:
                    check(label + ' missing prerequisite error', (cmd, bool(error)), (None, True))
                    check(label + ' error names prerequisite', 'xenia_edge' in error, True)
        check('persistent configuration untouched', cfg.read_bytes(), original)

        ra = interface(root, True, True)
        with patch.object(core, 'find_standalone_executable', return_value='/native/xenia_canary'):
            cmd, error = ra.build_launch_command(iso, 'Custom', core_name='xenia_edge', platform_slug='xbox360')
            check('explicit core beats native', (cmd[0], error), ('/usr/bin/retroarch', None))
            check('static classification stays standalone', core.standalone_emulator_for_platform(None, 'xbox360')[0], 'xenia')
            ra.get_available_cores = lambda: {}
            cmd, error = ra.build_launch_command(iso, 'Custom', core_name='xenia_edge', platform_slug='xbox360')
            check('explicit missing core never falls back', (cmd, error), (None, 'Core not found: xenia_edge'))
        normal_overlay = ra._launch_overlay_config()
        check('Vulkan does not leak into next launch overlay', 'video_driver' in Path(normal_overlay).read_text(), False)
        for executable in ('flatpak:org.libretro.RetroArch', 'flatpak:net.retrodeck.retrodeck'):
            ra = interface(root, False, True)
            ra.retroarch_executable = executable
            with patch.object(core, 'find_standalone_executable', return_value=''):
                cmd, error = ra.build_launch_command(iso, 'Custom', platform_slug='xbox360')
                check(executable + ' core launch', (cmd[0], error), ('flatpak', None))
                check(executable + ' Vulkan', 'video_driver = "vulkan"' in Path(cmd[-1]).read_text(), True)
                steam = core.SteamShortcutManager.__new__(core.SteamShortcutManager)
                steam.retroarch, steam.log = ra, lambda message: None
                exe, options = steam._build_launch_command(iso, 'Custom', 'xbox360')
                check(executable + ' Steam parity', shlex.split(exe) + shlex.split(options), cmd)
        check('core candidates canonical slug', ra._candidate_cores_for_platform('Custom', 'xbox360'), ['xenia_edge'])
        ra.get_available_cores = lambda: {}
        check('download candidate', ra.downloadable_cores_for_platform('Custom', 'xbox360'), ['xenia_edge'])

        for name in ('game.m3u', 'game.XCP'):
            path = touch(root / name)
            check(name + ' accepted by core', core.resolve_xbox360_rom(path, libretro=True), (path, root))
            try:
                core.resolve_xbox360_rom(path)
                check(name + ' refused by native', False, True)
            except ValueError:
                check(name + ' refused by native', True, True)
            archive = root / ('Archived-' + name + '.zip')
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('Wrapped/' + name, b'synthetic')
            with patch.object(core, 'find_standalone_executable', return_value='/native/xenia'):
                check(name + ' archive union independent of route', core._extract_xbox360_archive(archive), (True, ''))
            extracted = archive.with_suffix('') / name
            check(name + ' extracted unchanged', extracted.read_bytes(), b'synthetic')
        folder = root / 'Playlist'
        m3u = touch(folder / 'game.m3u')
        touch(folder / 'disc1.iso')
        touch(folder / 'disc2.iso')
        check('one playlist preferred', core.resolve_xbox360_rom(folder, libretro=True), (m3u, folder))
        touch(folder / 'other.m3u')
        try:
            core.resolve_xbox360_rom(folder, libretro=True)
            check('multiple playlists rejected', False, True)
        except ValueError:
            check('multiple playlists rejected', True, True)
        default = touch(folder / 'default.xex')
        check('default executable wins', core.resolve_xbox360_rom(folder, libretro=True), (default, folder))

        for machine in ('x86_64', 'aarch64'):
            with patch('platform.system', return_value='Linux'), patch('platform.machine', return_value=machine):
                base, ext = ra._buildbot_base()
                check(machine + ' official buildbot location', (base, ext),
                      (f'https://buildbot.libretro.com/nightly/linux/{machine}/latest', '.so'))
            response = SimpleNamespace(text='2026-11-15 abcdef01 xenia_edge_libretro.so.zip', raise_for_status=lambda: None)
            ra._buildbot_index_cache = None
            with patch.object(core, 'cache_dir', return_value=root / machine), \
                    patch.object(core.requests, 'get', return_value=response), \
                    patch.object(ra, '_buildbot_base', return_value=(base, ext)):
                index = core.RetroArchInterface.list_downloadable_cores(ra, force_refresh=True)
                check(machine + ' official catalogue filename', index['xenia_edge']['file'], 'xenia_edge_libretro.so.zip')
    print(f'{COUNT} assertions; {len(FAILED)} failed')
    return bool(FAILED)


if __name__ == '__main__':
    sys.exit(main())
