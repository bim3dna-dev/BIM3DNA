# -*- coding: utf-8 -*-
__title__ = "Update\nBIM3DNA"
__author__ = "BIMCode Solutions"
__doc__ = (
    "Checks the local BIM3DNA Git repository, compares the current branch with "
    "the configured upstream, reports local changes and remote commits, and "
    "optionally pulls updates using git pull --ff-only. No Revit document changes are made."
)

import json
import os
import fnmatch
import shutil
import subprocess
import tempfile
import time
import zipfile

try:
    from urllib2 import Request, urlopen, HTTPError, URLError
except Exception:
    try:
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError
    except Exception:
        Request = None
        urlopen = None
        HTTPError = Exception
        URLError = Exception

try:
    from pyrevit import script
except Exception:
    script = None

try:
    from pyrevit import forms
except Exception:
    forms = None


VERSION_FILE_NAME = "BIM3DNA.version.json"
GITHUB_OWNER = "BIM3DNA"
GITHUB_REPO = "BIM3DNARevitAPI"
RELEASE_ASSET_NAMES = [
    "BIM3DNA.extension.zip",
    "BIM3DNA_Toolbar_Update.zip",
]
UPDATE_CONFIG_FILE_NAME = "update_config.json"
PRIVATE_REPO_MESSAGE = (
    "This GitHub repository or release asset is not publicly accessible. "
    "The package updater cannot download private releases without authentication. "
    "For team deployment, either publish release assets somewhere accessible to the team "
    "or implement a secure authentication strategy. Do not embed a PAT in this script."
)


def get_output():
    if script is not None:
        try:
            return script.get_output()
        except Exception:
            pass
    return None


def emit(output, text):
    if output is not None:
        try:
            output.print_md(text)
            return
        except Exception:
            pass
    print(text)


def decode_process_text(value):
    if value is None:
        return ""
    try:
        if not isinstance(value, str):
            return value.decode("utf-8", "replace")
    except Exception:
        pass
    return value


def run_process(args, cwd=None):
    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        stdout, stderr = proc.communicate()
        return proc.returncode, decode_process_text(stdout), decode_process_text(stderr)
    except Exception as ex:
        return -1, "", str(ex)


def run_git(args, cwd):
    return run_process(["git"] + list(args), cwd=cwd)


def find_repo_root(start_dir):
    current = os.path.abspath(start_dir)
    while current and os.path.isdir(current):
        dotgit = os.path.join(current, ".git")
        if os.path.isdir(dotgit) or os.path.isfile(dotgit):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def find_extension_root(repo_root):
    candidate = os.path.join(repo_root, "BIM3DNA.extension")
    if os.path.isdir(candidate):
        return candidate
    current = os.path.abspath(os.path.dirname(__file__))
    while current and os.path.isdir(current):
        if os.path.basename(current).lower() == "bim3dna.extension":
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def find_installed_extension_root(start_dir):
    current = os.path.abspath(start_dir)
    while current and os.path.isdir(current):
        if os.path.basename(current).lower() == "bim3dna.extension":
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def read_json_file(path):
    try:
        if os.path.exists(path):
            with open(path, "r") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                return data, ""
    except Exception as ex:
        return None, str(ex)
    return None, ""


def read_version_file(repo_root):
    paths = [
        os.path.join(repo_root, VERSION_FILE_NAME),
        os.path.join(repo_root, "BIM3DNA.extension", VERSION_FILE_NAME),
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as fp:
                    data = json.load(fp)
                return path, data
            except Exception as ex:
                return path, {"error": str(ex)}
    return None, None


def read_extension_version_file(extension_root):
    path = os.path.join(extension_root, VERSION_FILE_NAME)
    data, error = read_json_file(path)
    if data is not None:
        return path, data
    if error:
        return path, {"error": error}
    return path, None


def read_update_source_config(extension_root):
    config = {
        "owner": GITHUB_OWNER,
        "repo": GITHUB_REPO,
        "asset_names": list(RELEASE_ASSET_NAMES),
        "channel": "stable",
    }
    path = os.path.join(extension_root, UPDATE_CONFIG_FILE_NAME)
    data, error = read_json_file(path)
    if data:
        if data.get("owner"):
            config["owner"] = data.get("owner")
        if data.get("repo"):
            config["repo"] = data.get("repo")
        if isinstance(data.get("asset_names"), list) and data.get("asset_names"):
            config["asset_names"] = data.get("asset_names")
        if data.get("channel"):
            config["channel"] = data.get("channel")
    return config, path if os.path.exists(path) else ""


def create_version_file_if_missing(repo_root):
    version_path, version_data = read_version_file(repo_root)
    if version_path:
        return version_path, False
    extension_root = find_extension_root(repo_root)
    if not extension_root:
        return None, False
    target = os.path.join(extension_root, VERSION_FILE_NAME)
    data = {
        "product": "BIM3DNA Revit Toolbar",
        "version": "0.1.0-dev",
        "channel": "dev",
        "build": "local",
        "update_mode": "git",
    }
    try:
        with open(target, "w") as fp:
            json.dump(data, fp, indent=2, sort_keys=True)
        return target, True
    except Exception:
        return None, False


def git_text(args, cwd):
    code, out, err = run_git(args, cwd)
    text = (out or "").strip()
    if not text:
        text = (err or "").strip()
    return code, text, out, err


def get_current_branch(repo_root):
    code, text, out, err = git_text(["branch", "--show-current"], repo_root)
    if code == 0 and text:
        return text, out, err
    code, text, out, err = git_text(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if code == 0:
        return text, out, err
    return "", out, err


def parse_ahead_behind(text):
    parts = text.replace("\t", " ").split()
    if len(parts) < 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except Exception:
        return None, None


def print_command_result(output, title, code, stdout, stderr):
    emit(output, "### {}".format(title))
    emit(output, "- Return code: `{}`".format(code))
    if stdout:
        emit(output, "")
        emit(output, "```text\n{}\n```".format(stdout.strip()))
    if stderr:
        emit(output, "")
        emit(output, "```text\n{}\n```".format(stderr.strip()))


def confirm_pull():
    message = "Remote updates are available. Pull BIM3DNA toolbar updates now?"
    if forms is None:
        return (
            False,
            "pyRevit forms are unavailable; aborting instead of pulling silently.",
        )
    try:
        result = forms.alert(message, yes=True, no=True, title="Update BIM3DNA")
        return bool(result), ""
    except Exception as ex:
        return False, "Could not show pull confirmation: {}".format(ex)


def reload_pyrevit_after_update(output):
    try:
        from pyrevit.loader import sessionmgr

        sessionmgr.reload_pyrevit()
        emit(output, "- pyRevit reload: `started`")
        return True
    except Exception as ex:
        emit(
            output,
            "- pyRevit reload: `failed` - Update completed, but automatic pyRevit reload failed. Please click pyRevit Reload or restart Revit. Reason: `{}`".format(
                ex
            ),
        )
        return False


def normalize_version(value):
    text = str(value or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    return text


def parse_version_tuple(value):
    text = normalize_version(value)
    parts = []
    for part in text.replace("-", ".").split("."):
        if part.isdigit():
            parts.append(int(part))
        else:
            break
    if not parts:
        return None
    return tuple(parts)


def compare_versions(local_version, remote_version):
    local_tuple = parse_version_tuple(local_version)
    remote_tuple = parse_version_tuple(remote_version)
    if local_tuple is None or remote_tuple is None:
        return None
    max_len = max(len(local_tuple), len(remote_tuple))
    local_tuple = local_tuple + tuple([0] * (max_len - len(local_tuple)))
    remote_tuple = remote_tuple + tuple([0] * (max_len - len(remote_tuple)))
    if remote_tuple > local_tuple:
        return 1
    if remote_tuple == local_tuple:
        return 0
    return -1


def confirm_yes_no(title, message):
    if forms is None:
        return False, "pyRevit forms are unavailable; aborting instead of updating silently."
    try:
        result = forms.alert(message, yes=True, no=True, title=title)
        return bool(result), ""
    except Exception as ex:
        return False, "Could not show confirmation: {}".format(ex)


def http_get_json(url):
    if Request is None or urlopen is None:
        return None, "Python HTTP support is unavailable."
    try:
        request = Request(url)
        try:
            request.add_header("User-Agent", "BIM3DNA-pyRevit-Updater")
            request.add_header("Accept", "application/vnd.github+json")
        except Exception:
            pass
        response = urlopen(request, timeout=30)
        data = response.read()
        if not isinstance(data, str):
            data = data.decode("utf-8", "replace")
        return json.loads(data), ""
    except HTTPError as ex:
        code = getattr(ex, "code", "")
        body = ""
        try:
            body = ex.read()
            if not isinstance(body, str):
                body = body.decode("utf-8", "replace")
        except Exception:
            pass
        return None, "HTTP {}: {}".format(code, body or ex)
    except URLError as ex:
        return None, "Network error: {}".format(ex)
    except Exception as ex:
        return None, str(ex)


def download_file(url, target_path):
    if Request is None or urlopen is None:
        return False, "Python HTTP support is unavailable."
    try:
        parent = os.path.dirname(target_path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        request = Request(url)
        try:
            request.add_header("User-Agent", "BIM3DNA-pyRevit-Updater")
        except Exception:
            pass
        response = urlopen(request, timeout=120)
        with open(target_path, "wb") as fp:
            while True:
                chunk = response.read(1024 * 128)
                if not chunk:
                    break
                fp.write(chunk)
        if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
            return True, ""
        return False, "Downloaded file is missing or empty."
    except HTTPError as ex:
        return False, "HTTP {}: {}".format(getattr(ex, "code", ""), ex)
    except Exception as ex:
        return False, str(ex)


def get_latest_release(config):
    url = "https://api.github.com/repos/{}/{}/releases/latest".format(
        config["owner"], config["repo"]
    )
    data, error = http_get_json(url)
    return url, data, error


def select_release_asset(release_data, asset_names):
    assets = release_data.get("assets") or []
    for wanted in asset_names:
        for asset in assets:
            if str(asset.get("name") or "").lower() == str(wanted).lower():
                return asset
    for asset in assets:
        name = str(asset.get("name") or "")
        if name == "BIM3DNA.extension.zip":
            return asset
        if name.startswith("BIM3DNA_Toolbar_Update_") and name.endswith(".zip"):
            return asset
    return None


def get_temp_update_root(version):
    return os.path.join(tempfile.gettempdir(), "BIM3DNA_Update", normalize_version(version) or "unknown")


def safe_makedirs(path):
    if path and not os.path.isdir(path):
        os.makedirs(path)


def clear_directory(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path)


def summarize_tree(root, max_rows):
    rows = []
    for current, dirs, files in os.walk(root):
        rel = os.path.relpath(current, root)
        if rel == ".":
            rel = ""
        for name in dirs + files:
            rows.append(os.path.join(rel, name))
            if len(rows) >= max_rows:
                rows.append("...")
                return rows
    return rows


def looks_like_extension_content(path):
    tab = os.path.join(path, "BIM3DNA.tab")
    if not os.path.isdir(tab):
        return False
    try:
        for name in os.listdir(tab):
            if name.lower().endswith(".panel") and os.path.isdir(os.path.join(tab, name)):
                return True
    except Exception:
        pass
    return False


def find_update_button(path):
    tab = os.path.join(path, "BIM3DNA.tab")
    if not os.path.isdir(tab):
        return ""
    for current, dirs, files in os.walk(tab):
        base = os.path.basename(current).lower()
        if base == "update.pushbutton" and "script.py" in files:
            return current
    return ""


def validate_staged_extension(staging_root):
    candidate = os.path.join(staging_root, "BIM3DNA.extension")
    if os.path.isdir(candidate):
        source = candidate
    elif looks_like_extension_content(staging_root):
        source = staging_root
    else:
        return None, ["No BIM3DNA.extension folder or extension content root found."], []

    errors = []
    warnings = []
    if not os.path.isdir(os.path.join(source, "BIM3DNA.tab")):
        errors.append("BIM3DNA.tab is missing.")
    if not looks_like_extension_content(source):
        errors.append("No .panel folder found under BIM3DNA.tab.")
    if not os.path.exists(os.path.join(source, VERSION_FILE_NAME)):
        warnings.append("BIM3DNA.version.json is missing; updater can create/update it.")
    if not find_update_button(source):
        errors.append("Update.pushbutton was not found under BIM3DNA.tab.")
    return source, errors, warnings


def should_preserve_file(name):
    lower = name.lower()
    return (
        lower == "create_print_set_pdf_config.json"
        or lower == "hvac_vertical_duct_tagging_config.json"
        or lower == "scanner_config.json"
        or fnmatch.fnmatch(lower, "*_config.json")
        or fnmatch.fnmatch(lower, "*.user.json")
    )


def should_preserve_dir(name):
    lower = name.lower()
    return lower in ["_user", "user_config", "logs", "results"]


def preserve_local_configs(extension_root, preserve_root, new_source):
    preserved = []
    if os.path.isdir(preserve_root):
        shutil.rmtree(preserve_root)
    os.makedirs(preserve_root)

    for current, dirs, files in os.walk(extension_root):
        rel_dir = os.path.relpath(current, extension_root)
        if rel_dir == ".":
            rel_dir = ""
        for dirname in list(dirs):
            src_dir = os.path.join(current, dirname)
            rel = os.path.join(rel_dir, dirname)
            if should_preserve_dir(dirname):
                dst_dir = os.path.join(preserve_root, rel)
                shutil.copytree(src_dir, dst_dir)
                preserved.append(rel)
        for filename in files:
            if should_preserve_file(filename):
                src = os.path.join(current, filename)
                rel = os.path.join(rel_dir, filename)
                dst = os.path.join(preserve_root, rel)
                safe_makedirs(os.path.dirname(dst))
                shutil.copy2(src, dst)
                preserved.append(rel)

    runtime_qpdf = os.path.join(extension_root, "_runtime", "qpdf")
    new_runtime_qpdf = os.path.join(new_source, "_runtime", "qpdf") if new_source else ""
    if os.path.isdir(runtime_qpdf) and not os.path.isdir(new_runtime_qpdf):
        dst = os.path.join(preserve_root, "_runtime", "qpdf")
        safe_makedirs(os.path.dirname(dst))
        shutil.copytree(runtime_qpdf, dst)
        preserved.append(os.path.join("_runtime", "qpdf"))
    return preserved


def restore_preserved_configs(preserve_root, extension_root):
    restored = []
    if not os.path.isdir(preserve_root):
        return restored
    for current, dirs, files in os.walk(preserve_root):
        rel_dir = os.path.relpath(current, preserve_root)
        if rel_dir == ".":
            rel_dir = ""
        for dirname in dirs:
            src_dir = os.path.join(current, dirname)
            rel = os.path.join(rel_dir, dirname)
            if should_preserve_dir(dirname) or rel.lower() == os.path.join("_runtime", "qpdf").lower():
                dst_dir = os.path.join(extension_root, rel)
                if os.path.isdir(dst_dir):
                    continue
                safe_makedirs(os.path.dirname(dst_dir))
                shutil.copytree(src_dir, dst_dir)
                restored.append(rel)
        for filename in files:
            if should_preserve_file(filename):
                src = os.path.join(current, filename)
                rel = os.path.join(rel_dir, filename)
                dst = os.path.join(extension_root, rel)
                safe_makedirs(os.path.dirname(dst))
                shutil.copy2(src, dst)
                restored.append(rel)
    return restored


def zip_directory(source_dir, zip_path):
    safe_makedirs(os.path.dirname(zip_path))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for current, dirs, files in os.walk(source_dir):
            for filename in files:
                path = os.path.join(current, filename)
                arc = os.path.relpath(path, os.path.dirname(source_dir))
                zf.write(path, arc)


def update_version_file(extension_root, release_data, config):
    path = os.path.join(extension_root, VERSION_FILE_NAME)
    data, error = read_json_file(path)
    if data is None:
        data = {"product": "BIM3DNA Revit Toolbar", "channel": "stable"}
    tag = release_data.get("tag_name") or ""
    data["version"] = normalize_version(tag)
    data["update_mode"] = "github_release"
    data["source"] = "github"
    data["github_owner"] = config["owner"]
    data["github_repo"] = config["repo"]
    data["github_release"] = tag
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if release_data.get("published_at"):
        data["build"] = release_data.get("published_at")
    with open(path, "w") as fp:
        json.dump(data, fp, indent=2, sort_keys=True)
    return path


def run_github_release_update(output, extension_root):
    emit(output, "## Update Mode")
    emit(output, "GitHub Release package mode")
    emit(output, "")
    emit(output, "This install is not inside a Git working tree. Switching to GitHub Release package update mode.")
    emit(output, "")

    source_config, config_path = read_update_source_config(extension_root)
    version_path, version_data = read_extension_version_file(extension_root)
    local_version = ""
    if version_data:
        local_version = version_data.get("version", "")

    emit(output, "## Local Install")
    emit(output, "- Extension root: `{}`".format(extension_root))
    emit(output, "- Update config: `{}`".format(config_path or "default constants"))
    emit(output, "")
    emit(output, "## Local Version")
    if version_data:
        emit(output, "- Version file: `{}`".format(version_path))
        emit(output, "- Version: `{}`".format(local_version or "unknown"))
        emit(output, "- Channel: `{}`".format(version_data.get("channel", "")))
        emit(output, "- Build: `{}`".format(version_data.get("build", "")))
        emit(output, "- Update mode: `{}`".format(version_data.get("update_mode", "")))
    else:
        emit(output, "No local BIM3DNA.version.json found. Local version is unknown.")
    emit(output, "")

    endpoint, release_data, release_error = get_latest_release(source_config)
    emit(output, "## GitHub Release")
    emit(output, "- Endpoint: `{}`".format(endpoint))
    if release_error:
        emit(output, "- Error: `{}`".format(release_error))
        if "401" in release_error or "403" in release_error or "404" in release_error:
            emit(output, PRIVATE_REPO_MESSAGE)
        else:
            emit(
                output,
                "GitHub Release update failed. If the repository is private, this updater cannot access it without authenticated access. Do not embed tokens in the updater.",
            )
        return

    asset = select_release_asset(release_data, source_config.get("asset_names", []))
    assets = release_data.get("assets") or []
    emit(output, "- Release tag: `{}`".format(release_data.get("tag_name", "")))
    emit(output, "- Release name: `{}`".format(release_data.get("name", "")))
    emit(output, "- Published: `{}`".format(release_data.get("published_at", "")))
    emit(output, "- Assets found: `{}`".format(", ".join([a.get("name", "") for a in assets]) or "-"))
    if release_data.get("body"):
        emit(output, "")
        emit(output, "### Release Notes")
        emit(output, "```text\n{}\n```".format(release_data.get("body", "").strip()[:4000]))
    if not asset:
        emit(output, "")
        emit(output, "Latest release exists, but no BIM3DNA extension update ZIP asset was found.")
        return

    remote_tag = release_data.get("tag_name", "")
    remote_version = normalize_version(remote_tag)
    comparison = compare_versions(local_version, remote_version)
    emit(output, "")
    emit(output, "- Selected asset: `{}`".format(asset.get("name", "")))
    emit(output, "- Asset size bytes: `{}`".format(asset.get("size", "")))
    emit(output, "- Local version normalized: `{}`".format(normalize_version(local_version) or "unknown"))
    emit(output, "- Remote version normalized: `{}`".format(remote_version or "unknown"))
    if comparison == 0:
        confirmed, reason = confirm_yes_no(
            "Update BIM3DNA",
            "BIM3DNA toolbar is already up to date.\n\nCurrent version: {}\nLatest version: {}\n\nForce reinstall from this release?".format(
                local_version or "unknown", remote_tag or "unknown"
            ),
        )
        if not confirmed:
            emit(output, "")
            emit(output, "## Install Result")
            emit(output, "BIM3DNA toolbar is already up to date. Reinstall was not performed.")
            if reason:
                emit(output, reason)
            return
    elif comparison is None:
        emit(output, "Version comparison could not be fully validated.")

    message = (
        "Update BIM3DNA Toolbar from GitHub Release?\n\n"
        "Current version: {}\n"
        "Latest version: {}\n"
        "Release: {}\n\n"
        "The current extension folder will be backed up before replacement.\n"
        "Revit may need to be restarted after update."
    ).format(local_version or "unknown", remote_tag or "unknown", release_data.get("name") or remote_tag)
    confirmed, reason = confirm_yes_no("Update BIM3DNA", message)
    if not confirmed:
        emit(output, "")
        emit(output, "## Install Result")
        emit(output, "Package update was not installed.")
        if reason:
            emit(output, reason)
        return

    update_root = get_temp_update_root(remote_version)
    asset_name = asset.get("name") or "BIM3DNA.extension.zip"
    download_path = os.path.join(update_root, asset_name)
    staging = os.path.join(update_root, "staging")
    preserved_root = os.path.join(update_root, "preserved_config")

    emit(output, "")
    emit(output, "## Download")
    if os.path.exists(download_path):
        try:
            os.remove(download_path)
        except Exception:
            pass
    ok, download_error = download_file(asset.get("browser_download_url"), download_path)
    emit(output, "- Download path: `{}`".format(download_path))
    if not ok:
        emit(output, "- Download failed: `{}`".format(download_error))
        return
    if not download_path.lower().endswith(".zip"):
        emit(output, "- Download failed: selected asset is not a .zip file.")
        return
    emit(output, "- Download size bytes: `{}`".format(os.path.getsize(download_path)))

    emit(output, "")
    emit(output, "## Validation")
    try:
        clear_directory(staging)
        with zipfile.ZipFile(download_path, "r") as zf:
            zf.extractall(staging)
    except Exception as ex:
        emit(output, "- ZIP extraction failed: `{}`".format(ex))
        return
    new_source, validation_errors, validation_warnings = validate_staged_extension(staging)
    if validation_errors or validation_warnings:
        for item in validation_errors:
            emit(output, "- {}".format(item))
        for item in validation_warnings:
            emit(output, "- Warning: {}".format(item))
    if not new_source or validation_errors:
        emit(output, "")
        emit(output, "Extracted folder summary:")
        emit(output, "```text\n{}\n```".format("\n".join(summarize_tree(staging, 80))))
        return
    emit(output, "- Validated source folder: `{}`".format(new_source))

    emit(output, "")
    emit(output, "## Preserved Config")
    preserved = preserve_local_configs(extension_root, preserved_root, new_source)
    if preserved:
        emit(output, "```text\n{}\n```".format("\n".join(preserved)))
    else:
        emit(output, "No matching local config files found.")
    emit(output, "- Sidecar external tools config is not modified: `%APPDATA%\\BIM3DNA\\external_tools.json`")

    emit(output, "")
    emit(output, "## Backup")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(os.environ.get("APPDATA") or tempfile.gettempdir(), "BIM3DNA", "Backups")
    backup_zip = os.path.join(
        backup_dir,
        "BIM3DNA.extension_{}_{}.zip".format(normalize_version(local_version) or "unknown", timestamp),
    )
    try:
        zip_directory(extension_root, backup_zip)
    except Exception as ex:
        emit(output, "- Backup failed: `{}`".format(ex))
        emit(output, "Update aborted before modifying the live extension.")
        return
    emit(output, "- Backup zip: `{}`".format(backup_zip))

    emit(output, "")
    emit(output, "## Install Result")
    installing_backup = extension_root + ".__backup_installing_" + timestamp
    try:
        if os.path.exists(installing_backup):
            shutil.rmtree(installing_backup)
        os.rename(extension_root, installing_backup)
        shutil.copytree(new_source, extension_root)
        restored = restore_preserved_configs(preserved_root, extension_root)
        version_file = update_version_file(extension_root, release_data, source_config)
        emit(output, "- Replacement completed.")
        emit(output, "- Temporary install backup folder: `{}`".format(installing_backup))
        emit(output, "- Version file updated: `{}`".format(version_file))
        if restored:
            emit(output, "- Restored preserved config entries: `{}`".format(len(restored)))
    except Exception as ex:
        emit(output, "- Install failed: `{}`".format(ex))
        rollback_ok = False
        try:
            if os.path.isdir(extension_root):
                shutil.rmtree(extension_root)
            if os.path.isdir(installing_backup):
                os.rename(installing_backup, extension_root)
                rollback_ok = True
        except Exception as rollback_ex:
            emit(output, "- Rollback failed: `{}`".format(rollback_ex))
        if rollback_ok:
            emit(output, "- Rollback completed.")
        return

    emit(output, "")
    emit(output, "## Reload Result")
    reload_pyrevit_after_update(output)
    emit(output, "")
    emit(output, "## Next Action")
    emit(output, "For safest behavior, restart Revit before using updated BIM3DNA tools.")


def main():
    output = get_output()
    emit(output, "# BIM3DNA Update")
    emit(output, "")
    emit(
        output,
        "This updater uses Git working-tree mode when available, otherwise GitHub Release package mode. It does not modify any Revit document.",
    )
    emit(output, "")

    script_dir = os.path.abspath(os.path.dirname(__file__))
    repo_root = find_repo_root(script_dir)
    if not repo_root:
        extension_root = find_installed_extension_root(script_dir)
        if not extension_root:
            emit(output, "## Update Result")
            emit(output, "Could not resolve BIM3DNA.extension root for package update mode.")
            return
        run_github_release_update(output, extension_root)
        return

    version_created_path, created_version = create_version_file_if_missing(repo_root)
    version_path, version_data = read_version_file(repo_root)

    emit(output, "## Repository")
    emit(output, "- Script directory: `{}`".format(script_dir))
    emit(output, "- Repository root: `{}`".format(repo_root))
    if version_data:
        emit(output, "- Version file: `{}`".format(version_path))
        emit(output, "- Product: `{}`".format(version_data.get("product", "")))
        emit(output, "- Version: `{}`".format(version_data.get("version", "")))
        emit(output, "- Channel: `{}`".format(version_data.get("channel", "")))
        emit(output, "- Build: `{}`".format(version_data.get("build", "")))
        emit(output, "- Commit: `{}`".format(version_data.get("commit", "")))
    elif created_version:
        emit(output, "- Version file created: `{}`".format(version_created_path))
    else:
        emit(output, "- Version file: `No BIM3DNA.version.json found.`")
    emit(output, "")

    git_code, git_out, git_err = run_process(["git", "--version"], cwd=repo_root)
    if git_code != 0:
        emit(output, "## Update Result")
        emit(
            output,
            "Git was not found on PATH. Install Git for Windows or add git.exe to PATH.",
        )
        print_command_result(output, "git --version", git_code, git_out, git_err)
        return

    code_top, top_out, top_err = run_git(["rev-parse", "--show-toplevel"], repo_root)
    code_remote, remote_out, remote_err = run_git(["remote", "-v"], repo_root)
    branch, branch_out, branch_err = get_current_branch(repo_root)
    code_status, status_out, status_err = run_git(["status", "--porcelain"], repo_root)
    code_head, head_out, head_err = run_git(["rev-parse", "--short", "HEAD"], repo_root)
    code_last, last_out, last_err = run_git(
        ["log", "-1", "--pretty=format:%h %cd %s", "--date=iso"], repo_root
    )

    emit(output, "## Current Version")
    emit(output, "- Git: `{}`".format((git_out or "").strip()))
    emit(output, "- Git top-level: `{}`".format((top_out or top_err).strip()))
    emit(output, "- Current branch: `{}`".format(branch or "<unknown>"))
    emit(output, "- Current HEAD: `{}`".format((head_out or head_err).strip()))
    emit(output, "- Last commit: `{}`".format((last_out or last_err).strip()))
    emit(output, "")
    emit(output, "### Remotes")
    emit(output, "```text\n{}\n```".format((remote_out or remote_err or "").strip()))
    emit(output, "")

    emit(output, "## Remote Status")
    fetch_code, fetch_out, fetch_err = run_git(["fetch", "--prune"], repo_root)
    print_command_result(output, "git fetch --prune", fetch_code, fetch_out, fetch_err)
    if fetch_code != 0:
        emit(output, "## Update Result")
        emit(
            output,
            "Fetch failed. Authentication, network access, or remote configuration may be the issue. Pull was not attempted.",
        )
        return

    up_code, up_out, up_err = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], repo_root
    )
    upstream = (up_out or "").strip()
    if up_code != 0 or not upstream:
        emit(output, "## Update Result")
        emit(output, "Current branch has no upstream tracking branch.")
        if branch:
            emit(output, "")
            emit(output, "Suggested manual command:")
            emit(
                output,
                "```text\ngit branch --set-upstream-to=origin/{} {}\n```".format(
                    branch, branch
                ),
            )
        return

    count_code, count_out, count_err = run_git(
        ["rev-list", "--left-right", "--count", "HEAD...@{u}"], repo_root
    )
    ahead, behind = parse_ahead_behind(count_out)
    if count_code != 0 or ahead is None or behind is None:
        emit(output, "## Update Result")
        emit(output, "Could not compare local branch with upstream.")
        print_command_result(
            output,
            "git rev-list --left-right --count HEAD...@{u}",
            count_code,
            count_out,
            count_err,
        )
        return

    emit(output, "- Upstream: `{}`".format(upstream))
    emit(output, "- Ahead count: `{}`".format(ahead))
    emit(output, "- Behind count: `{}`".format(behind))
    emit(output, "")

    emit(output, "## Local Changes")
    if status_out.strip():
        emit(output, "```text\n{}\n```".format(status_out.strip()))
    else:
        emit(output, "Working tree is clean.")
    emit(output, "")

    if behind == 0 and ahead == 0:
        emit(output, "## Update Result")
        emit(output, "BIM3DNA toolbar is already up to date.")
        emit(output, "")
        emit(output, "## Next Action")
        emit(output, "No action required.")
        return

    if ahead > 0 and behind == 0:
        emit(output, "## Update Result")
        emit(output, "Local branch has commits not on upstream. Pull not required.")
        emit(output, "")
        emit(output, "## Next Action")
        emit(output, "Push or manage local commits manually if needed.")
        return

    if ahead > 0 and behind > 0:
        emit(output, "## Update Result")
        emit(
            output,
            "Local and remote branches have diverged. Automatic update is blocked.",
        )
        emit(output, "")
        emit(output, "## Next Action")
        emit(
            output,
            "Resolve manually with Git. No reset, rebase, or force operation was attempted.",
        )
        return

    if status_out.strip():
        emit(output, "## Update Result")
        emit(
            output,
            "Local working tree is not clean. Automatic update is blocked to avoid overwriting local work.",
        )
        emit(output, "")
        emit(output, "Suggested manual commands:")
        emit(output, "```text\ngit status\ngit stash\ngit pull --ff-only\n```")
        return

    emit(output, "## Remote Commits")
    log_code, log_out, log_err = run_git(["log", "--oneline", "HEAD..@{u}"], repo_root)
    if log_code == 0 and log_out.strip():
        emit(output, "```text\n{}\n```".format(log_out.strip()))
    else:
        print_command_result(
            output, "git log --oneline HEAD..@{u}", log_code, log_out, log_err
        )
    emit(output, "")

    confirmed, confirm_reason = confirm_pull()
    if not confirmed:
        emit(output, "## Update Result")
        emit(output, "Update was not pulled.")
        if confirm_reason:
            emit(output, confirm_reason)
        emit(output, "")
        emit(output, "## Next Action")
        emit(
            output,
            "Run the updater again and accept the prompt, or run `git pull --ff-only` manually.",
        )
        return

    emit(output, "## Update Result")
    pull_code, pull_out, pull_err = run_git(["pull", "--ff-only"], repo_root)
    print_command_result(output, "git pull --ff-only", pull_code, pull_out, pull_err)
    if pull_code != 0:
        emit(output, "")
        emit(output, "Update failed. No forced recovery was attempted.")
        return

    new_code, new_out, new_err = run_git(
        ["log", "-1", "--pretty=format:%h %cd %s", "--date=iso"], repo_root
    )
    emit(output, "")
    emit(output, "- Update completed successfully.")
    emit(output, "- New HEAD: `{}`".format((new_out or new_err).strip()))
    reload_pyrevit_after_update(output)
    emit(output, "")
    emit(output, "## Next Action")
    emit(
        output,
        "If pyRevit did not reload automatically, click pyRevit Reload or restart Revit.",
    )


if __name__ == "__main__":
    main()
