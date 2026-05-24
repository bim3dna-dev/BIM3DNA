# -*- coding: utf-8 -*-
from pyrevit.forms import WPFWindow
import platform
import pyrevit
import os


class AboutForm:
    def __init__(self):
        try:
            self.window = WPFWindow("AboutUI.xaml")
            self.logo_button = self.window.FindName("LogoButton")
            self.logo_image = self.window.FindName("LogoImage")
            self.set_logo_image()
            self.logo_button.Click += self.navigate_to_uri
            self.window.FindName("CloseButton").Click += self.button_close
            self.version_data = self.fetch_versions()
            self.update_ui()
            self.window.show()
        except Exception as ex:
            print("Initialization Error: {}".format(ex))

    def set_logo_image(self):
        """Load the local BIM3DNA logo via an absolute bundle path."""
        try:
            import clr

            clr.AddReference("PresentationCore")
            clr.AddReference("WindowsBase")
            from System import Uri, UriKind
            from System.Windows.Media.Imaging import BitmapImage

            bundle_dir = os.path.dirname(__file__)
            logo_path = os.path.join(bundle_dir, "BIM3DNA Logo Final.png")
            if not os.path.exists(logo_path):
                raise IOError("Logo asset not found: {}".format(logo_path))

            bitmap = BitmapImage()
            bitmap.BeginInit()
            bitmap.UriSource = Uri(logo_path, UriKind.Absolute)
            bitmap.EndInit()

            self.logo_image.Source = bitmap
        except Exception as ex:
            print("Logo Load Error: {}".format(ex))

    def fetch_versions(self):
        """Fetch all versions properly."""
        try:
            import platform
            import clr  # .NET
            import System.Reflection

            revit_version = "Unknown"
            revit_build = "Unknown"
            pyrevit_version = "Unknown"
            active_engine = "Unknown"
            active_engine_version = "Unknown"
            installed_cpython_version = "Unknown"

            # Revit Version
            try:
                revit_version = __revit__.Application.VersionName
                revit_build = __revit__.Application.VersionBuild
            except:
                pass

            # Active Engine
            try:
                active_engine = platform.python_implementation()
                active_engine_version = platform.python_version()
            except:
                pass

            # pyRevit Version
            try:
                pyrevit_dll = clr.LoadAssemblyByName("pyRevitLabs.Common")
                pyrevit_version_info = pyrevit_dll.GetName().Version
                pyrevit_version = str(pyrevit_version_info)
            except:
                try:
                    pyrevit_addon = __revit__.GetAddon("pyRevit")
                    pyrevit_version = pyrevit_addon.Version.ToString()
                except:
                    pass

            # CPython Version installed (if available)
            try:
                import os

                env_cpyver = os.environ.get("PYREVIT_CPYVERSION")
                if env_cpyver and len(env_cpyver) >= 4:
                    major = env_cpyver[0]
                    minor = env_cpyver[1:3]
                    patch = env_cpyver[3]
                    installed_cpython_version = "{}.{}.{}".format(major, minor, patch)
                else:
                    installed_cpython_version = "3.12.3"
            except:
                installed_cpython_version = "3.12.3"

            return {
                "RevitVersion": revit_version,
                "RevitBuild": revit_build,
                "pyRevitVersion": pyrevit_version,
                "ActiveEngine": active_engine,
                "ActiveEngineVersion": active_engine_version,
                "InstalledCPythonVersion": installed_cpython_version,
            }

        except Exception as ex:
            print("Version Fetch Error: {}".format(ex))
            return {
                "RevitVersion": "Unknown",
                "RevitBuild": "Unknown",
                "pyRevitVersion": "Unknown",
                "ActiveEngine": "Unknown",
                "ActiveEngineVersion": "Unknown",
                "InstalledCPythonVersion": "Unknown",
            }

    def update_ui(self):
        """Update the UI."""
        self.window.RevitVersionText.Text = "Revit Version: {} (Build {})".format(
            self.version_data.get("RevitVersion", "Unknown"),
            self.version_data.get("RevitBuild", "Unknown"),
        )
        self.window.pyRevitVersionText.Text = "pyRevit Version: {}".format(
            self.version_data.get("pyRevitVersion", "Unknown")
        )
        self.window.ActiveIronPythonVersionText.Text = (
            "Active Python Engine Version: {} {}".format(
                self.version_data.get("ActiveEngine", "Unknown"),
                self.version_data.get("ActiveEngineVersion", "Unknown")
            )
        )
        self.window.ActiveCPythonVersionText.Text = (
            "Detected CPython Version: {}".format(
                self.version_data.get("InstalledCPythonVersion", "Unknown")
            )
        )

    def navigate_to_uri(self, sender, e):
        """Open company website."""
        try:
            import webbrowser

            webbrowser.open("https://bim3dna.nl/", new=2)
        except:
            pass

    def button_close(self, sender, e):
        """Close the window."""
        self.window.Close()


# Run
if __name__ == "__main__":
    AboutForm()
