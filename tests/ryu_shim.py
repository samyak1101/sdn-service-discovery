import importlib
import importlib.abc
import importlib.util
import sys


class _RyuAlias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name == "ryu" or name.startswith("ryu."):
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(spec.name.replace("ryu", "os_ken", 1))

    def exec_module(self, module):
        pass


def install():
    try:
        import ryu
        return "ryu"
    except ImportError:
        pass
    sys.meta_path.insert(0, _RyuAlias())
    from os_ken.base import app_manager
    if not hasattr(app_manager, "RyuApp"):
        app_manager.RyuApp = app_manager.OSKenApp
    return "os_ken"
