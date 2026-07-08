# -*- coding: utf-8 -*-

from Autodesk.Revit.UI import TaskDialog


def print_report(title, lines):
    print("")
    print("## {}".format(title))
    for line in lines:
        print(line)


def show_report(title, lines, max_chars=3800):
    message = "\n".join([str(x) for x in lines])
    if len(message) > max_chars:
        message = message[:max_chars] + "\n\n...output truncated; see pyRevit console."
    TaskDialog.Show(title, message)


def report_result(title, result):
    lines = []
    lines.append("Status: {}".format("SUCCESS" if result.get("ok") else "FAILED"))
    if result.get("reason"):
        lines.append("Reason: {}".format(result.get("reason")))
    for key in sorted(result.keys()):
        if key in ["ok", "reason"]:
            continue
        lines.append("{}: {}".format(key, result.get(key)))
    print_report(title, lines)
    show_report(title, lines)

