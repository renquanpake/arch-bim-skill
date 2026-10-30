"""arch-bim-skill 外部路径集中配置。

配置文件默认位于本模块同目录的 config.json，可用环境变量 ARCHBIM_CONFIG 指向另一份。
单项覆盖使用 ARCHBIM_<LEAF_UPPER>，例如 ARCHBIM_REQUEST_DIR、ARCHBIM_ACAD_EXE。
"""

import copy
import json
import os

CONFIG_ENV = "ARCHBIM_CONFIG"
CONFIG_NAME = "config.json"

DEFAULTS = {
    "paths": {
        "acadExe": "D:\\cad\\AutoCAD 2021\\acad.exe",
        "accoreconsoleExe": "D:\\cad\\AutoCAD 2021\\accoreconsole.exe",
        "cadJsonDll": "D:\\cad\\AutoCAD 2021\\CadJsonPlugin.dll",
        "revitExe": "D:\\Revit 2022\\Revit.exe",
        "requestDir": "%USERPROFILE%\\Documents\\Marvis\\ai-modeling",
        "dwgOutputDir": "%TEMP%\\arch-bim-out",
    }
}

ENV_KEYS = {
    "acadExe": "ARCHBIM_ACAD_EXE",
    "accoreconsoleExe": "ARCHBIM_ACCORECONSOLE_EXE",
    "cadJsonDll": "ARCHBIM_CADJSON_DLL",
    "revitExe": "ARCHBIM_REVIT_EXE",
    "requestDir": "ARCHBIM_REQUEST_DIR",
    "dwgOutputDir": "ARCHBIM_DWG_OUTPUT_DIR",
}

FILE_SUFFIXES = (".exe", ".dll", ".exe.config", ".cmd", ".bat", ".ps1", ".py")


def _config_path():
    custom = os.environ.get(CONFIG_ENV)
    if custom:
        return os.path.expanduser(os.path.expandvars(custom))
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_NAME)


def load():
    """读取配置；文件缺失或损坏时返回内置默认值，不抛异常。"""
    path = _config_path()
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("config root is not an object")
        merged = copy.deepcopy(DEFAULTS)
        for section, values in data.items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                merged[section].update(values)
            else:
                merged[section] = values
        return merged
    except Exception:
        return copy.deepcopy(DEFAULTS)


def _env_name(leaf):
    if leaf in ENV_KEYS:
        return ENV_KEYS[leaf]
    out = []
    for idx, ch in enumerate(leaf):
        if ch.isupper() and idx > 0:
            out.append("_")
        out.append(ch.upper())
    return "ARCHBIM_" + "".join(out)


def get(dotted_key, default=None):
    """按点号取配置项，环境变量 ARCHBIM_<LEAF_UPPER> 优先。"""
    parts = [p for p in str(dotted_key).split(".") if p]
    if not parts:
        return default
    override = os.environ.get(_env_name(parts[-1]))
    if override:
        return override
    node = load()
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def resolve(value):
    """展开 %VAR% 与 ~，返回字符串。"""
    if value is None:
        return ""
    return os.path.expanduser(os.path.expandvars(str(value)))


def _looks_like_file(leaf, value):
    leaf_l = leaf.lower()
    value_l = str(value).lower()
    if leaf_l.endswith(("dir", "folder")):
        return False
    if leaf_l.endswith(("exe", "dll")) or value_l.endswith(FILE_SUFFIXES):
        return True
    return os.path.splitext(value_l)[1] != ""


def check(keys):
    """返回不存在路径项的描述列表，空列表表示全部存在。"""
    missing = []
    for key in keys:
        value = get(key)
        if value is None or str(value).strip() == "":
            missing.append("%s: 未配置" % key)
            continue
        path = resolve(value)
        leaf = str(key).split(".")[-1]
        if _looks_like_file(leaf, path):
            if not os.path.isfile(path):
                missing.append("%s: %s 不存在" % (key, path))
        else:
            if not os.path.isdir(path):
                missing.append("%s: %s 不存在" % (key, path))
    return missing
