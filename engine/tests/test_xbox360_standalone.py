"""Native Xenia builds: download/launch only. Run as a script."""
import io
import os
import shlex
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from romm_sync_engine import sync_core as core

FAILED = []
COUNT = 0


def check(name, got, want):
    global COUNT
    COUNT += 1
    ok = got == want
    print('ok  ' if ok else 'FAIL', name, '' if ok else f'{got!r} != {want!r}')
    if not ok:
        FAILED.append(name)


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'game')
    return path


def zipped(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name in entries:
            z.writestr(name, b'game')
    return buf.getvalue()


class Settings:
    def __init__(self, **paths):
        self.paths = paths

    def get(self, section, key, default=''):
        return self.paths.get(key, default)


def download(dest, slug, body, folder=False):
    details = {'fs_name': dest.name, 'platform_slug': slug,
               'fs_extension': '' if folder else dest.suffix.lstrip('.'),
               'has_multiple_files': folder}

    class Response:
        status_code = 200
        headers = {'content-length': str(len(body))}

        def json(self):
            return details

        def iter_content(self, chunk_size):
            yield body

        def close(self):
            pass

    client = core.RomMClient.__new__(core.RomMClient)
    client.ensure_authenticated = lambda: True
    client.base_url = 'https://romm.invalid'
    client.session = type('Session', (), {'get': lambda *a, **kw: Response()})()
    return client.download_rom(1, dest.name, dest)


def main():
    for name, slug, expected in (
            ('PlayStation 3', 'ps3', None),
            ('Microsoft Xbox 360', None, None), ('Xbox 360', None, None),
            ('unknown', 'xbox360', 'xenia'), ('xbox360', None, 'xenia'),
            ('xbox360', 'xbox', None), ('xbox360', '', None),
            ('Xbox', 'xbox', None), ('PlayStation', 'ps', None),
            ('PlayStation 2', 'ps2', None),
            ('unknown', 'xbox3600', None), ('PlayStation Vita', 'vita', None)):
        match = core.standalone_emulator_for_platform(name, slug)
        check(f'platform {name}/{slug}', match[0] if match else None, expected)
    for slug in ('xbox-360', 'xbox_360', 'microsoft-xbox-360', 'xbox3600', 'prefix-xbox360'):
        check(f'noncanonical slug {slug}', core.standalone_emulator_for_platform('xbox360', slug), None)
        check(f'noncanonical positional slug {slug}', core.standalone_emulator_for_platform(slug), None)
    check('Xenia advertises only canonical slug', core.STANDALONE_EMULATORS['xenia']['platforms'], ('xbox360',))

    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, HOME=tmp), \
            patch.object(core, 'flatpak_app_installed', return_value=False), \
            patch.object(core.shutil, 'which', return_value=None):
        home = Path(tmp)
        builds = {}
        for filename in ('xenia_canary_linux.AppImage', 'xenia_edge_linux.AppImage',
                         'xenia.AppImage', 'xenia_helper.AppImage'):
            app = touch(home / 'AppImages' / filename)
            app.chmod(0o755)
            builds[filename] = str(app)
        xs = core.STANDALONE_EMULATORS['xenia']
        found = core.find_standalone_builds('xenia', xs)
        check('only real native variants listed', [(b['label'], Path(b['path']).name) for b in found],
              [('Xenia Canary', 'xenia_canary_linux.AppImage'),
               ('Xenia Edge', 'xenia_edge_linux.AppImage')])
        check('Xenia has no guessed Flatpak', xs.get('flatpak_id'), None)
        ra = core.RetroArchInterface.__new__(core.RetroArchInterface)
        ra.retroarch_executable = ''
        xbox = home / "Xbox 360 game's folder"
        xex = touch(xbox / 'default.xex')
        touch(xbox / 'media/data.bin')
        check('Xenia folder', core.resolve_xbox360_rom(xbox), (xex, xbox))
        check('Xenia direct xex', core.resolve_xbox360_rom(xex), (xex, xbox))
        iso = touch(home / 'Game.iso')
        check('Xenia direct iso', core.resolve_xbox360_rom(iso), (iso, home))
        for variant in ('canary', 'edge'):
            app = builds[f'xenia_{variant}_linux.AppImage']
            ra.settings = Settings(xenia_path=app)
            check(f'{variant} selection', core.find_standalone_executable('xenia', xs, ra.settings), app)
            check(f'{variant} launch without RA', ra.build_launch_command(xbox, 'Any name', platform_slug='xbox360'),
                  ([app, str(xex)], None))
        steam = core.SteamShortcutManager.__new__(core.SteamShortcutManager)
        steam.retroarch, steam.log = ra, lambda message: None
        for slug, path in (('xbox360', touch(home / 'game.zip')), ('xbox360', home)):
            cmd, error = ra.build_launch_command(path, 'unknown', platform_slug=slug)
            check(f'{slug} unsupported {path.name}', (cmd, bool(error)), (None, True))
        ra.settings = Settings(xenia_path=builds['xenia_edge_linux.AppImage'])
        steam.retroarch = ra
        exe, options = steam._build_launch_command(xbox, 'unknown', 'xbox360')
        check('Steam native quoting', (exe, shlex.split(options)),
              ('"' + builds['xenia_edge_linux.AppImage'] + '"', [str(xex)]))
        steam.cover_manager = None
        entry = steam.build_shortcut_entry('Game', str(xbox), 'unknown', 'Collection',
                                           platform_slug='xbox360')
        check('collection shortcut propagates slug',
              (entry['Exe'], shlex.split(entry['LaunchOptions'])), (exe, [str(xex)]))
        ra.suggest_core_for_platform = lambda name: ('snes9x', '/cores/snes9x.so')
        for executable, expected in (
                ('/usr/bin/retroarch', ('"/usr/bin/retroarch"', '-L "/cores/snes9x.so" "game.sfc"')),
                ('flatpak', ('flatpak', 'run org.libretro.RetroArch -L "/cores/snes9x.so" "game.sfc"')),
                ('snap', ('snap', 'run retroarch -L "/cores/snes9x.so" "game.sfc"')),
                ('flatpak:retrodeck', ('"flatpak"', 'run net.retrodeck.retrodeck "game.sfc"'))):
            ra.retroarch_executable = executable
            check(f'Steam RA unchanged {executable}',
                  steam._build_launch_command('game.sfc', 'SNES'), expected)
        ra.retroarch_executable = ''
        with patch.object(core, 'find_standalone_executable', return_value=''):
            entry = steam.build_shortcut_entry('Missing', str(home), 'unknown',
                                               'Collection', platform_slug='xbox360')
            check('missing shortcut names standalone',
                  (entry['Exe'], 'Xenia' in entry['LaunchOptions'],
                   'RetroArch' in entry['LaunchOptions']), ('/usr/bin/echo', True, False))
        entry = steam.build_shortcut_entry('Unsupported', str(home / 'game.zip'), 'Xbox 360',
                                           'Collection', platform_slug='xbox360')
        check('unsupported shortcut shows actual boot error',
              ('Xenia' in entry['LaunchOptions'], '.iso/.xex' in entry['LaunchOptions']), (True, True))

        members = ['default.xex', 'media/nested/data.bin']
        body = zipped(members)
        for folder in (False, True):
            dest = home / 'xbox360' / ('Folder' if folder else 'Archive.zip')
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok, why = download(dest, 'xbox360', body, folder)
            root = dest if folder else dest.with_suffix('')
            check(f'folder={folder} download succeeds', (ok, why.startswith('Download successful')), (True, True))
            check(f'folder={folder} complete hierarchy',
                  sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()), sorted(members))
            check(f'folder={folder} resolves', core.resolve_xbox360_rom(root)[1], root)
        wrapped = home / 'xbox360' / 'Wrapped.zip'
        wrapped.write_bytes(zipped(['Title/' + n for n in members]))
        check('wrapper extracted', core._extract_xbox360_archive(wrapped), (True, ''))
        check('wrapper bootable',
              core.resolve_xbox360_rom(wrapped.with_suffix(''))[1], wrapped.with_suffix(''))
        direct = home / 'Direct.iso'
        check('direct single file download', download(direct, 'xbox360', b'disc')[0], True)
        check('direct single file bytes', direct.read_bytes(), b'disc')
        for body in (b'broken zip', zipped(['readme.txt']), zipped(['../escape'])):
            bad = home / 'Bad.zip'
            bad.write_bytes(body)
            ok, why = core._extract_xbox360_archive(bad)
            check('bad archive rejected meaningfully', (ok, 'archive kept' in why), (False, True))
            check('bad archive preserved', bad.read_bytes(), body)
            check('no bad extraction installed', bad.with_suffix('').exists(), False)
        check('traversal ZIP wrote nothing outside staging', (home / 'escape').exists(), False)
        link_archive = home / 'Link.zip'
        with zipfile.ZipFile(link_archive, 'w') as z:
            info = zipfile.ZipInfo('link')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, '../outside')
            z.writestr('default.xex', b'game')
        ok, why = core._extract_xbox360_archive(link_archive)
        check('symlink ZIP rejected', (ok, 'symbolic links' in why), (False, True))
        check('symlink ZIP retained', link_archive.exists(), True)
        bad_folder = home / 'BadFolder'
        ok, why = download(bad_folder, 'xbox360', b'broken zip', True)
        check('folder failure meaningful', (ok, 'archive kept at' in why), (False, True))
        check('folder failure archive retained', len(list(home.glob('tmp*.zip'))), 1)
        with patch('tempfile.TemporaryDirectory', side_effect=OSError('disk full')):
            bad = home / 'TempFailure.zip'
            bad.write_bytes(zipped(['default.xex']))
            ok, why = core._extract_xbox360_archive(bad)
            check('temp failure reports error', (ok, 'disk full' in why), (False, True))
            check('temp failure archive retained', bad.exists(), True)
    print(f'{COUNT} assertions; {len(FAILED)} failed')
    return bool(FAILED)


if __name__ == '__main__':
    sys.exit(main())
