# -*- coding: utf-8 -*-
import os
import subprocess
import sys

agent_dir = r"C:\Zonneveld\AI_Mesh_Agent_Wizard"
python_exe = r"C:\Zonneveld\Zonneveld_Revit_API_2\.venv\Scripts\python.exe"
agent_script = os.path.join(agent_dir, "cpython_script.py")

if not os.path.exists(agent_script):
    print("❌ CPython script not found:", agent_script)
else:
    print("🚀 Launching CPython mesh importer...")
    subprocess.Popen([python_exe, agent_script])
