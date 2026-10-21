"""Plugin lifecycle: background recognition, inline guard execution, idle playback."""
import copy
import threading
import uuid

from macroflow.core.storage import registered_module_object, resolve_path, save_app_settings
from macroflow.execution.interval_plugin import IntervalConditionPlugin
from macroflow.execution.player import PlaybackStopped


_claim_lock = threading.Lock()


def condition_matches(app, condition):
    key = str(condition.get('module_key') or '')
    obj = registered_module_object(key)
    if obj is None or obj.get('recognize', 'image') not in ('image', 'text'):
        raise ValueError('定时插件的条件模块不存在或不是图片/文字模块')
    region = obj.get('region') or []
    guard = {'name': '定时条件执行', 'key': 'plugin:interval', 'module_ref': True,
             'module_key': key, 'template': resolve_path(str(obj.get('template') or condition.get('template') or '')),
             'threshold': float(obj.get('threshold', .85)),
             'region': tuple(region) if len(region) == 4 else None,
             'region_mode': 'custom' if len(region) == 4 else 'screen'}
    guard.update(recognize=obj.get('recognize', 'image'),
                 expected_text=str(obj.get('expected_text') or ''),
                 match_mode=str(obj.get('match_mode') or 'contains'))
    detector = app._guard_text_detect if guard['recognize'] == 'text' else app._guard_image_detect
    detected, _ = detector(guard, None, None)
    if guard.get('warned_missing_template') or guard.get('warned_find_error'):
        raise RuntimeError('条件识别未完成，请检查模板和识别引擎')
    return bool(detected)


def get_interval_plugins(app):
    plugins = app.__dict__.get('_interval_plugins')
    if plugins is not None:
        return plugins
    plugins = app._interval_plugins = {}
    configs = app.app_settings.get('interval_condition_plugins') or []
    if not isinstance(configs, list):
        configs = []
    for config in configs:
        try:
            plugin = _configured_plugin(config)
            key = plugin.config['id']
            if key in plugins:
                raise ValueError('插件 ID 重复')
            plugins[key] = plugin
        except (ValueError, TypeError) as exc:
            app._ui(app._log, f'定时插件未载入：{exc}')
    closed = app._interval_plugins_closed = threading.Event()

    def monitor():
        errors = {}
        while not closed.wait(.25):
            if getattr(app, 'exiting', False) or app.player.paused:
                continue
            recorder = getattr(app, 'recorder', None)
            if recorder is not None and recorder.running:
                continue
            for key, plugin in list(plugins.items()):
                try:
                    plugin.check(lambda condition: condition_matches(app, condition))
                    errors.pop(key, None)
                except Exception as exc:
                    if str(exc) != errors.get(key):
                        app._ui(app._log, f"插件[{plugin.config['name']}]条件识别失败：{exc}")
                        errors[key] = str(exc)

    threading.Thread(target=monitor, name='MacroFlowIntervalPlugins', daemon=True).start()

    def destroy(event):
        if event.widget is app.root:
            closed.set()
            for plugin in list(plugins.values()):
                plugin.disable()
            app.player.stop()

    app.root.bind('<Destroy>', destroy, add='+')
    app.root.after(250, lambda: _idle_tick(app))
    return plugins


def _configured_plugin(config):
    if not isinstance(config, dict) or not isinstance(config.get('id'), str) or not config['id'].strip():
        raise ValueError('插件 ID 无效')
    name = str(config.get('name') or '').strip()
    if not name or len(name) > 80:
        raise ValueError('插件名称须为 1 至 80 个字符')
    plugin = IntervalConditionPlugin()
    plugin.configure({**config, 'name': name})
    return plugin


def save_interval_plugin(app, config):
    plugins = get_interval_plugins(app)
    checked = _configured_plugin(config)
    key = checked.config['id']
    configs = [copy.deepcopy(checked.config if other_key == key else plugin.config)
               for other_key, plugin in list(plugins.items())]
    if key not in plugins:
        configs.append(copy.deepcopy(checked.config))
    settings = {**app.app_settings, 'interval_condition_plugins': configs,
                'disconnect_monitor_enabled': bool(getattr(app, 'disconnect_monitor_enabled', False))}
    settings.pop('interval_condition_plugin', None)
    save_app_settings(settings)
    app.app_settings = settings
    if key in plugins:
        plugins[key].configure(checked.config)
    else:
        plugins[key] = checked


def add_interval_plugin(app):
    plugins = get_interval_plugins(app)
    key = uuid.uuid4().hex
    save_interval_plugin(app, {'id': key, 'name': f'定时插件 {len(plugins) + 1}',
                              'enabled': True, 'interval_ms': 3_600_000, 'condition': None, 'actions': []})
    return key


def delete_interval_plugin(app, key):
    plugins = get_interval_plugins(app)
    if key not in plugins:
        return
    settings = {**app.app_settings, 'interval_condition_plugins':
                [copy.deepcopy(p.config) for other, p in list(plugins.items()) if other != key]}
    save_app_settings(settings)
    removed = plugins.pop(key)
    removed.disable()
    app.app_settings = settings


def stop_interval_plugins(app):
    """F12/恢复重启：卸下插件的运行状态，但不改动用户保存的启用开关。

    插件是否启用是用户偏好（config['enabled']，_collect_sidebar_settings 会落盘）；
    停止执行只应让当前这次运行闭嘴，不能顺手把偏好改成关闭。
    """
    plugins = app.__dict__.get('_interval_plugins')
    if plugins is not None:
        for plugin in list(plugins.values()):
            plugin.disable()  # F12 never waits for recognition or Tk callbacks.


def arm_interval_plugins(app):
    """每次开始执行时按用户偏好重新武装插件（F12 只是临时卸下）。"""
    plugins = app.__dict__.get('_interval_plugins')
    if plugins is not None:
        for plugin in list(plugins.values()):
            plugin.arm()


def _claim_ready_plugin(plugins):
    # The workflow worker and Tk idle callback share one player and one claim boundary.
    with _claim_lock:
        entries = list(plugins.values())
        if any(plugin.running for plugin in entries):
            return None
        for plugin in entries:
            claimed = plugin.claim()
            if claimed is not None:
                return plugin, claimed
        return None


def claim_plugin_hit(app):
    plugins = app.__dict__.get('_interval_plugins')
    if (not plugins or app.player.paused or app.player.stop_event.is_set()
            or app.player.__dict__.get('_plugin_action_scope_ids') is not None):
        return None
    selected = _claim_ready_plugin(plugins)
    if selected is None:
        return None
    plugin, claimed = selected
    generation = claimed['generation']
    try:
        hwnd = app._bound_hwnd(update_display=False)
    except Exception:
        plugin.finish(generation, False)
        raise
    return {'kind': 'success', 'log_subject': f"插件 · {plugin.config['name']}", 'delay_ms': 0,
            'actions': claimed['actions'], 'hwnd': hwnd, 'condition_result': claimed['condition_result'],
            '_plugin_valid': lambda: plugin.valid(generation),
            '_plugin_complete': lambda success: plugin.finish(generation, success)}


def _idle_tick(app):
    plugins = app.__dict__.get('_interval_plugins')
    closed = app.__dict__.get('_interval_plugins_closed')
    if plugins is None or closed.is_set() or getattr(app, 'exiting', False):
        return
    try:
        worker = getattr(app, 'worker', None)
        recorder = getattr(app, 'recorder', None)
        busy = ((worker is not None and worker.is_alive()) or app.player.running or app.player.paused
                or (recorder is not None and recorder.running) or getattr(app, '_hotkey_script_running', False))
        if not busy:
            selected = _claim_ready_plugin(plugins)
            if selected is not None:
                _start_idle_plugin(app, *selected)
    except Exception as exc:
        app._log(f'定时插件启动失败：{exc}')
    finally:
        if not closed.is_set():
            app.root.after(250, lambda: _idle_tick(app))


def _start_idle_plugin(app, plugin, claimed):
    generation = claimed['generation']
    try:
        hwnd = app._bound_hwnd(update_display=False)
        activate = bool(app.activate_target_enabled_var.get())
    except Exception:
        plugin.finish(generation, False)
        raise

    def execute():
        old_callback = app.player.on_action_start
        success = False
        def before_action(event):
            if not plugin.valid(generation):
                raise PlaybackStopped()
            if old_callback is not None:
                old_callback(event)
        app.player.on_action_start = before_action
        try:
            if not plugin.valid(generation):
                return
            name = plugin.config.get('name', '定时条件执行')
            app._ui(app._log, f'插件 · {name}：开始执行')
            app.player.play(claimed['actions'], hwnd=hwnd, activate_target=activate,
                            script_name=f'插件 · {name}')
            success = not app.player.stop_event.is_set()
        except Exception as exc:
            app._ui(app._log, f'定时插件执行失败：{exc}')
        finally:
            app.player.on_action_start = old_callback
            plugin.finish(generation, success)
            app._ui(app._refresh_execution_pause_controls)

    app.workflow_stop.clear()
    app.worker = threading.Thread(target=execute, name='MacroFlowPluginActions', daemon=True)
    app.worker.start()
    app._refresh_execution_pause_controls()
