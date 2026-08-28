"""Install the bundled qrenderdoc bridge extension."""

import os
import shutil
import tempfile


EXTENSION_NAME = "renderdoc_mcp_bridge"


def extension_source_dir():
    return os.path.join(os.path.dirname(__file__), "qrenderdoc_extension")


def default_extension_roots():
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return []
    return [
        os.path.join(appdata, "qrenderdoc", "extensions"),
        os.path.join(appdata, "RenderPro", "extensions"),
    ]


def install_bridge(roots=None):
    src = extension_source_dir()
    if not os.path.isdir(src):
        raise RuntimeError("Bridge source not found: %s" % src)

    installed = []
    for root in roots or default_extension_roots():
        if not root:
            continue
        if not os.path.isdir(root):
            os.makedirs(root)
        dest = os.path.join(root, EXTENSION_NAME)
        preset_backup = _backup_presets(dest)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        _restore_presets(preset_backup, os.path.join(dest, "presets"))
        installed.append(dest)
    return installed


def _backup_presets(dest):
    presets = os.path.join(dest, "presets")
    if not os.path.isdir(presets):
        return None
    backup = tempfile.mkdtemp(prefix="renderdoc_mcp_presets_")
    shutil.copytree(presets, os.path.join(backup, "presets"))
    return os.path.join(backup, "presets")


def _restore_presets(backup, presets):
    if not backup or not os.path.isdir(backup):
        return
    if os.path.isdir(presets):
        shutil.rmtree(presets)
    shutil.copytree(backup, presets)
    shutil.rmtree(os.path.dirname(backup), ignore_errors=True)


def main():
    installed = install_bridge()
    for path in installed:
        print(path)


if __name__ == "__main__":
    main()
