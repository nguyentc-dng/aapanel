import sys
import json
from importlib.machinery import ExtensionFileLoader
from importlib.util import module_from_spec, spec_from_loader

loader = ExtensionFileLoader(
    "PluginLoader",
    "/www/server/panel/class/PluginLoader.x86_64.Python3.12.so"
)
spec = spec_from_loader("PluginLoader", loader)

sys.modules.pop("PluginLoader", None)

real = module_from_spec(spec)
loader.exec_module(real)

# Patch get_plugin_list function
_org_get_plugin_list = real.get_plugin_list

def _patch_plugin_list(data):
    if not isinstance(data, dict):
        return data
    data['pro'] = 0   # 0 = lifetime license (isLifetime=true in frontend)
    data['ltd'] = -1  # -1 = no enterprise license
    data['status'] = True
    data['aln'] = '4FVqcu3NbuQfzX9q+uPTLw=='
    plugin_list = data.get('list', [])
    for p in plugin_list:
        if not isinstance(p, dict):
            continue
        et = p.get('endtime')
        if et is None or (isinstance(et, (int, float)) and et < 0):
            p['endtime'] = 0
        if p.get('authorization_sn') is None:
            p['authorization_sn'] = '0'
    return data

def _get_plugin_list(*args, **kwargs):
    data = _org_get_plugin_list(*args, **kwargs)
    return _patch_plugin_list(data)

real.get_plugin_list = _get_plugin_list

# Patch get_auth_state function
def _get_auth_state(*args, **kwargs):
    try:
        softList = _get_plugin_list()
        if softList['ltd'] > -1:
            return 2
        elif softList['pro'] > -1:
            return 1
        else:
            return 0
    except:
        return -1

real.get_auth_state = _get_auth_state

# Patch parse_plugin_list function
def _parse_plugin_list(*args, **kwargs):
    return True

real.parse_plugin_list = _parse_plugin_list

sys.modules["PluginLoader"] = real
