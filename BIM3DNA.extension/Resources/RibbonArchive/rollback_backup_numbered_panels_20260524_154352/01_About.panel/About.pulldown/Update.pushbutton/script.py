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
import subprocess

try:
    from pyrevit import script
except Exception:
    script = None

try:
    from pyrevit import forms
except Exception:
    forms = None


VERSION_FILE_NAME = "BIM3DNA.version.json"


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


def main():
    output = get_output()
    emit(output, "# BIM3DNA Update")
    emit(output, "")
    emit(
        output,
        "This updater uses the local Git working tree only. It does not modify any Revit document.",
    )
    emit(output, "")

    script_dir = os.path.abspath(os.path.dirname(__file__))
    repo_root = find_repo_root(script_dir)
    if not repo_root:
        emit(output, "## Update Result")
        emit(
            output,
            "This BIM3DNA extension is not inside a Git working tree. Git-backed update is unavailable.",
        )
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
