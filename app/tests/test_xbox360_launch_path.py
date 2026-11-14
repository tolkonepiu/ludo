"""Standalone executable trees must never go through the RetroArch disc picker."""
from types import SimpleNamespace

import pytest

import ludo_app.backend as module
from ludo_app.backend import LudoBackend, _detect_multi_disc, _resolve_download_path
from ludo_app.host import HostProfile


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    return LudoBackend(host=HostProfile('1.0.0', '-x86_64.AppImage',
                                      tmp_path / 'runtime', 'desktop'))


def make(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'game')
    return path


@pytest.mark.parametrize('slug,name', [
    ('xbox360', 'game.iso'), ('xbox360', 'game.xex'),
    ('xbox360', 'game.zar'), ('xbox360', 'game.ZAR'),
])
def test_single_file_is_validated(backend, tmp_path, slug, name):
    target = make(tmp_path / name)
    assert backend._resolve_launch_path(target, platform_slug=slug) == str(target)


@pytest.mark.parametrize('name', ['default.xex', 'game.iso', 'game.zar', 'game.ZAR'])
def test_folder_bypasses_generic_disc_lister(backend, tmp_path, monkeypatch, name):
    folder = tmp_path / 'Game'
    target = make(folder / name)
    make(folder / 'manual.pdf')
    make(folder / 'content' / 'a.bin')
    make(folder / 'playlist.m3u')
    monkeypatch.setattr(backend, '_list_local_discs', lambda _: pytest.fail('disc lister called'))
    assert backend._resolve_launch_path(folder, 'stale.m3u', 'xbox360') == str(target)
    assert _detect_multi_disc(folder, True, 'xbox360') == (False, 0)


@pytest.mark.parametrize('name', ['game.zip', 'game.pkg', 'game.elf', 'arbitrary.bin', 'extensionless'])
def test_unsupported_formats_have_actionable_error(backend, tmp_path, name):
    target = make(tmp_path / name)
    with pytest.raises(ValueError, match=r'Xenia.*\.iso/\.xex/\.zar'):
        backend._resolve_launch_path(target, platform_slug='xbox360')


def test_missing_executable_is_not_launchable(backend, tmp_path):
    assert backend._resolve_launch_path(tmp_path / 'game.xex', platform_slug='xbox360') is None


@pytest.mark.parametrize('slug', ['xbox-360', 'xbox_360', 'microsoft-xbox-360',
                                 'xbox3600', 'prefix-xbox360'])
def test_noncanonical_slug_uses_generic_disc_lister(backend, monkeypatch, tmp_path, slug):
    make(tmp_path / 'default.xex')
    monkeypatch.setattr(backend, '_list_local_discs', lambda _: [
        {'name': 'generic', 'path': 'generic-target'}])
    assert backend._resolve_launch_path(tmp_path, platform_slug=slug) == 'generic-target'


async def test_disc_picker_bypass(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(backend, '_games_index', lambda: {7: {
        'romm_data': {'platform_slug': 'xbox360'}, 'is_downloaded': True,
        'local_path': str(tmp_path)}})
    monkeypatch.setattr(backend, '_list_local_discs', lambda _: pytest.fail('disc lister called'))
    assert await backend.get_local_discs(7) == {'success': True, 'discs': [], 'last': ''}


@pytest.mark.parametrize('method', ['launch_game', 'prepare_steam_launch'])
async def test_launch_rpc_surfaces_invalid_target_error(backend, monkeypatch, tmp_path, method):
    target = make(tmp_path / 'game.pkg')
    monkeypatch.setattr(backend, '_games_index', lambda: {7: {
        'romm_data': {'platform_slug': 'xbox360'}, 'is_downloaded': True,
        'local_path': str(target)}})
    backend._retroarch = SimpleNamespace(_gamescope_running=lambda: True)
    result = await getattr(backend, method)(7)
    assert result['success'] is False
    assert 'Xenia' in result['message'] and '.iso/.xex/.zar' in result['message']


@pytest.mark.parametrize('name', ['default.xex', 'game.zar', 'game.ZAR'])
def test_extracted_download_survives_list_and_reconcile(backend, tmp_path, name):
    folder = tmp_path / 'xbox360' / 'Game'
    target = make(folder / name)
    make(folder / 'assets' / 'a.bin')
    make(folder / 'assets' / 'b.bin')
    assert _resolve_download_path(tmp_path, 'xbox360', 'Game.zip') == (folder, True)
    rom = {'id': 7, 'platform_slug': 'xbox360', 'fs_name': 'Game.zip'}
    entry = backend._game_entry(rom, tmp_path)
    assert entry['is_downloaded'] and entry['local_path'] == str(folder)
    assert not entry['is_multi_disc'] and entry['disc_count'] == 0
    assert backend._resolve_launch_path(folder, platform_slug='xbox360') == str(target)
    backend._settings = SimpleNamespace(get=lambda *args: str(tmp_path))
    backend._reconcile_downloads([entry])
    assert entry['is_downloaded'] and entry['local_path'] == str(folder)
    assert not entry['is_multi_disc'] and entry['disc_count'] == 0


def test_generic_retroarch_disc_selection_unchanged(backend, tmp_path):
    make(tmp_path / 'disc1.iso')
    disc2 = make(tmp_path / 'disc2.iso')
    playlist = make(tmp_path / 'game.m3u')
    assert backend._resolve_launch_path(tmp_path, platform_slug='psx') == str(playlist)
    assert backend._resolve_launch_path(tmp_path, 'disc2.iso', 'psx') == str(disc2)
    assert _detect_multi_disc(tmp_path, True, 'psx') == (True, 3)


async def test_build_rpc_accepts_registry_key_without_retroarch(backend, monkeypatch):
    builds = [{'path': '/AppImages/xenia_edge_linux.AppImage', 'current': True}]
    backend._retroarch = None
    monkeypatch.setattr(module, 'find_standalone_builds', lambda *args: builds)
    out = await backend.list_emulator_builds('xenia')
    assert out['success'] and out['key'] == 'xenia' and out['builds'] == builds


async def test_status_rpc_keeps_standalones_when_retroarch_missing(backend):
    status = {'installed': False, 'standalone': {
        'xenia': {'installed': True}}}
    backend._retroarch = SimpleNamespace(emulator_status=lambda: status)
    assert await backend.get_emulator_status() == {'success': True, **status}
