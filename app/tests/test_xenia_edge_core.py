"""Backend routing contracts against the real engine's Xenia resolution."""
from unittest.mock import Mock

import pytest

from romm_sync_engine import sync_core as engine
from ludo_app.backend import LudoBackend
from ludo_app.host import HostProfile


def make(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'game')
    return path


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(engine, 'config_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr(engine, '_standalone_appimage_dirs', lambda: [tmp_path / 'AppImages'])
    monkeypatch.setattr(engine.shutil, 'which', lambda _: None)
    monkeypatch.setattr(engine, 'flatpak_app_installed', lambda _: False)
    # Discovery boundaries only: route selection, settings, mappings and target
    # validation below are the real engine, not copies of its decisions.
    monkeypatch.setattr(engine.RetroArchInterface, '_resolve_installation', lambda _: None)
    ra = engine.RetroArchInterface()
    ra.test_cores = {}
    monkeypatch.setattr(ra, 'get_available_cores', lambda: ra.test_cores)
    monkeypatch.setattr(ra, 'core_download_support', lambda: {'available': True, 'reason': ''})
    monkeypatch.setattr(ra, '_gamescope_running', lambda: True)
    app = LudoBackend(host=HostProfile('1', '-x86_64.AppImage', tmp_path / 'rt', 'desktop'))
    app._retroarch = ra
    app._settings = ra.settings
    app._available_games = [{'rom_id': 7, 'platform_slug': 'xbox360',
                             'platform': 'Xbox 360', 'is_downloaded': True,
                             'local_path': str(tmp_path / 'game.iso')}]
    return app


def native(tmp_path):
    path = make(tmp_path / 'AppImages/xenia_canary_linux.AppImage')
    path.chmod(0o755)
    return str(path)


@pytest.mark.parametrize('has_native', [False, True])
@pytest.mark.parametrize('has_core', [False, True])
@pytest.mark.parametrize('pin', ['', 'xenia_edge'])
async def test_mapping_get_and_set_return_identical_complete_row(
        backend, tmp_path, has_native, has_core, pin):
    if has_native:
        native(tmp_path)
    if has_core:
        backend._retroarch.test_cores['xenia_edge'] = str(tmp_path / 'xenia_edge_libretro.so')
    changed = await backend.set_core_override('xbox360', pin)
    listed = await backend.get_core_mappings()
    assert changed['success'] and listed['success']
    row = changed['mapping']
    assert listed['mappings'] == [row]
    core_route = bool(pin) or not has_native
    assert row['standalone']['launch_core'] == ('xenia_edge' if core_route else '')
    assert row['standalone']['installed'] == has_native
    assert row['override'] == pin
    assert row['platform_cores'] == (['xenia_edge'] if has_core else [])
    assert row['download_candidates'] == ([] if has_core else ['xenia_edge'])
    if not core_route:
        assert (row['resolved_core'], row['source']) == ('Xenia', 'standalone')
    elif has_core:
        assert (row['resolved_core'], row['source']) == ('xenia_edge', 'override' if pin else 'guess')
    else:
        assert (row['resolved_core'], row['source']) == ('', 'none')
    assert engine.SettingsManager().get('CoreOverrides', 'xbox360', '') == pin


@pytest.mark.parametrize('pin', ['snes9x', 'xenia', 'XENIA_EDGE', '../xenia_edge'])
async def test_invalid_core_pin_rejected_without_mutation(backend, pin):
    backend._settings.set('CoreOverrides', 'xbox360', 'xenia_edge')
    assert not (await backend.set_core_override('xbox360', pin))['success']
    assert backend._settings.get('CoreOverrides', 'xbox360') == 'xenia_edge'


async def test_auto_clears_existing_pin_and_returns_native_row(backend, tmp_path):
    native(tmp_path)
    await backend.set_core_override('xbox360', 'xenia_edge')
    changed = await backend.set_core_override('xbox360', '')
    assert changed['success']
    assert changed['mapping']['override'] == ''
    assert changed['mapping']['resolved_core'] == 'Xenia'
    assert changed['mapping']['standalone']['launch_core'] == ''
    assert engine.SettingsManager().get('CoreOverrides', 'xbox360', '') == ''


@pytest.mark.parametrize('live_interface', [False, True])
@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('ra_installed', [False, True])
async def test_native_selection_clears_pin_and_refreshes(backend, tmp_path, monkeypatch,
                                                       live_interface, automatic, ra_installed):
    path = native(tmp_path)
    ra = backend._retroarch
    ra.retroarch_executable = '/retroarch' if ra_installed else ''
    backend._settings.set('CoreOverrides', 'xbox360', 'xenia_edge')
    refresh = Mock(wraps=ra.refresh_installation)
    monkeypatch.setattr(ra, 'refresh_installation', refresh)
    monkeypatch.setattr(ra, 'emulator_status', lambda: {
        'installed': False, 'standalone': ra.standalone_emulators_status()})
    if not live_interface:
        backend._retroarch = None
    assert await backend.set_emulator_build('xenia', '' if automatic else path) == {'success': True}
    assert engine.SettingsManager().get('CoreOverrides', 'xbox360', '') == ''
    assert backend._settings.get('Emulators', 'xenia_path') == ('' if automatic else path)
    assert ra.standalone_emulator_status('Xbox 360', 'xbox360')['launch_core'] == ''
    if live_interface:
        assert refresh.call_count == int(ra_installed)
        status = await backend.get_emulator_status()
        xenia = next(s for s in status['standalone'] if s['key'] == 'xenia')
        assert xenia['installed'] and xenia['launch_core'] == ''
    else:
        refresh.assert_not_called()


@pytest.mark.parametrize('has_native', [False, True])
async def test_pinned_missing_core_offers_installer_not_native_fallback(backend, tmp_path, has_native):
    if has_native:
        native(tmp_path)
    await backend.set_core_override('xbox360', 'xenia_edge')
    game = backend._available_games[0]
    gap = backend._core_gap(game, 'Xbox 360')
    assert gap['needs_core'] and gap['can_download']
    assert gap['candidates'] == ['xenia_edge']
    assert gap['platform_slug'] == 'xbox360'


def test_no_native_or_core_offers_core_installation(backend):
    gap = backend._core_gap(backend._available_games[0], 'Xbox 360')
    assert gap['needs_core'] and gap['candidates'] == ['xenia_edge']


def test_installed_native_suppresses_missing_core(backend, tmp_path):
    native(tmp_path)
    assert backend._core_gap(backend._available_games[0], 'Xbox 360') == {}


@pytest.mark.parametrize('method', ['launch_game', 'prepare_steam_launch'])
@pytest.mark.parametrize('extension', ['m3u', 'xcp'])
@pytest.mark.parametrize('core_route', [False, True])
async def test_direct_and_deck_launch_resolve_selected_formats(backend, tmp_path, monkeypatch,
                                                            method, extension, core_route):
    native(tmp_path)
    if core_route:
        await backend.set_core_override('xbox360', 'xenia_edge')
    target = make(tmp_path / f'game.{extension}')
    backend._available_games[0]['local_path'] = str(target)
    resolved = []

    async def ready(game, path):
        resolved.append(path)
        return None, 'resolved target'

    monkeypatch.setattr(backend, '_ready_to_boot', ready)
    monkeypatch.setattr(backend, '_list_local_discs', lambda _: pytest.fail('generic disc lister'))
    out = await getattr(backend, method)(7)
    assert not out['success']
    if core_route:
        assert resolved == [str(target)] and out['message'] == 'resolved target'
    else:
        assert not resolved and 'Xenia' in out['message']


@pytest.mark.parametrize('nested', [False, True])
async def test_xbox_has_no_prelaunch_save_state_or_bios_side_effects(backend, monkeypatch, nested):
    game = {'romm_data': {'platform_slug': 'xbox360'}} if nested else {'platform_slug': 'xbox360'}
    class Forbidden:
        def __getattr__(self, name):
            pytest.fail(f'Xbox touched save/state sync: {name}')

    backend._auto_sync = Forbidden()
    backend._romm_client = Forbidden()
    monkeypatch.setattr(backend, '_bios_dir', lambda: pytest.fail('Xbox BIOS lookup'))
    assert await backend._pre_launch_sync(game, '/game.xex') is None
    assert backend._resume_entry_slot(game, 'xenia_edge') is None
    assert backend._bios_gap(game, 'Xbox 360') == {}
    backend._auto_sync = None
    monkeypatch.setattr('asyncio.sleep', lambda _: pytest.fail('Xbox waited for save sync'))
    assert await backend._pre_launch_sync(game) is None
