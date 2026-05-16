# -*- coding: utf-8 -*-
import json
import os


def _ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def get_local_state_dir():
    root = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if not root:
        root = os.path.expanduser("~")
    return _ensure_dir(os.path.join(root, "BIM3DNA", "AI"))


class LocalSettingsStore(object):
    def __init__(self, file_name):
        self.path = os.path.join(get_local_state_dir(), file_name)

    def load(self, defaults=None):
        data = {}
        if defaults:
            data.update(defaults)
        if not os.path.exists(self.path):
            return data
        try:
            with open(self.path, "r") as stream:
                loaded = json.load(stream)
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:
            pass
        return data

    def save(self, data):
        try:
            with open(self.path, "w") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
            return True
        except Exception:
            return False
