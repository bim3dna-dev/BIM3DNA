# -*- coding: utf-8 -*-
__title__ = "Manage\nWorksets"
__doc__ = (
    """Create and view worksets with a clean UI. Unsupported actions show a message."""
)

from Autodesk.Revit.DB import (
    Transaction,
    Workset,
    WorksetKind,
    FilteredWorksetCollector,
)
from pyrevit import revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms

import clr

clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Windows.Forms import (
    Application,
    Form,
    Label,
    Button,
    TextBox,
    ToolTip,
    ListBox,
    ScrollBars,
    DialogResult,
    FormBorderStyle,
    FormStartPosition,
    AnchorStyles,
    OpenFileDialog,
)
from System.Drawing import Point, Size, Font

doc = revit.doc

def _silent_alert(msg, title="pyRevit", exitscript=False, **kwargs):
    # No icon => no Windows system sound
    WinForms.MessageBox.Show(
        str(msg), str(title),
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.None
    )
    if exitscript:
        pyscript.exit()

# Replace pyRevit's alert with our silent version
forms.alert = _silent_alert
# --- end silent override---

def get_user_workset_names():
    return sorted(
        [
            ws.Name
            for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)
        ]
    )


class WorksetManagerForm(Form):
    def __init__(self):
        self.Text = "Manage Worksets"
        self.Width = 720
        self.Height = 530
        self.MinimumSize = Size(540, 320)
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.StartPosition = FormStartPosition.CenterScreen

        font = Font("Segoe UI", 9)  # keeps font creation tidy
        tooltip = ToolTip()

        # Labels
        self.label_new = Label(Text="New Worksets (one per line):")
        self.label_new.Location = Point(20, 10)
        self.label_new.Size = Size(300, 20)
        self.label_new.Font = font
        self.label_new.Anchor = AnchorStyles.Top | AnchorStyles.Left
        self.Controls.Add(self.label_new)

        self.label_existing = Label(Text="Existing Worksets:")
        self.label_existing.Location = Point(370, 10)
        self.label_existing.Size = Size(300, 20)
        self.label_existing.Font = font
        self.label_existing.Anchor = AnchorStyles.Top | AnchorStyles.Right
        self.Controls.Add(self.label_existing)

        # Input Box
        self.input_box = TextBox()
        self.input_box.Multiline = True
        self.input_box.ScrollBars = ScrollBars.Vertical
        self.input_box.Location = Point(20, 35)
        self.input_box.Size = Size(300, 370)
        self.input_box.Font = font
        self.input_box.Anchor = (
            AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left
        )
        self.Controls.Add(self.input_box)

        # Existing Worksets List
        self.existing_listbox = ListBox()
        self.existing_listbox.Location = Point(370, 35)
        self.existing_listbox.Size = Size(300, 370)
        self.existing_listbox.Font = font
        self.existing_listbox.Anchor = (
            AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Right
        )
        self.Controls.Add(self.existing_listbox)

        self.load_worksets()

        # Buttons (bottom row, left > right)
        button_width = 100
        button_height = 30
        start_x = 20
        spacing = 10
        y_pos = 420

        def place(x_index):
            return Point(start_x + x_index * (button_width + spacing), y_pos)

        # Buttons (aligned bottom left/right)
        self.create_button = Button(Text="Create Worksets")
        self.create_button.Location = place(0)
        self.create_button.Size = Size(button_width, button_height)
        self.create_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.create_button.Click += self.create_worksets
        self.Controls.Add(self.create_button)

        # Import button (NEW)
        self.import_button = Button(Text="Import...")
        self.import_button.Location = place(1)  # <-- fixed math
        self.import_button.Size = Size(button_width, button_height)
        self.import_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.import_button.Click += self.import_worksets
        self.Controls.Add(self.import_button)

        self.set_button = Button(Text="Set Active")
        self.set_button.Location = place(2)
        self.set_button.Size = Size(button_width, button_height)
        self.set_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.set_button.Click += self.show_unsupported
        self.Controls.Add(self.set_button)

        self.rename_button = Button(Text="Rename")
        self.rename_button.Location = place(3)
        self.rename_button.Size = Size(button_width, button_height)
        self.rename_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.rename_button.Click += self.show_unsupported
        self.Controls.Add(self.rename_button)

        self.delete_button = Button(Text="Delete")
        self.delete_button.Location = place(4)
        self.delete_button.Size = Size(button_width, button_height)
        self.delete_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.delete_button.Click += self.show_unsupported
        self.Controls.Add(self.delete_button)

        self.close_button = Button(Text="Close")
        self.close_button.Location = place(5)
        self.close_button.Size = Size(button_width, button_height)
        self.close_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.close_button.Click += self.close_form
        self.Controls.Add(self.close_button)

        # Info Label
        self.note_label = Label(
            Text="Note: Worksets with duplicate names will be skipped."
        )
        self.note_label.Location = Point(20, 460)
        self.note_label.Size = Size(500, 20)
        self.note_label.Font = font
        self.note_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.note_label)

    def load_worksets(self):
        self.existing_listbox.Items.Clear()
        for name in get_user_workset_names():
            self.existing_listbox.Items.Add(name)

    # Import worksets from .txt
    def import_worksets(self, sender, args):
        dlg = OpenFileDialog()
        dlg.Filter = "Text files (*.txt)|*.txt|All files (*.*)|*.*"
        dlg.Title = "Select Worksets File"

        if dlg.ShowDialog() == DialogResult.OK:
            try:
                # read as utf-8-sig to strip possible BOM
                with open(dlg.FileName, "rb") as f:
                    content = f.read().decode("utf-8-sig")
                self.input_box.Text = content.strip()
            except Exception as e:
                forms.alert(
                    "Import Error\n\nCould not read file: {}".format(e), title="Error"
                )

    def create_worksets(self, sender, args):
        input_text = self.input_box.Text.strip()
        if not input_text:
            forms.alert("Please enter at least one workset name.")
            return

        names = [n.strip() for n in input_text.splitlines() if n.strip()]
        existing = set(get_user_workset_names())  # <-- make it a set

        created = 0
        skipped = []
        for name in names:
            if name in existing:
                skipped.append(name + " (already exists)")
                continue
            t = Transaction(doc, "Create Workset: " + name)
            try:
                t.Start()
                Workset.Create(doc, name)
                t.Commit()
                created += 1
                existing.add(name)
            except Exception as e:
                skipped.append(name + " (error: " + str(e) + ")")
                try:
                    if t.HasStarted():
                        t.RollBack()
                except:
                    pass

        self.load_worksets()
        msg = "Workset creation complete.\n\nCreated: {}\nSkipped: {}\n".format(
            created, len(skipped)
        )
        if skipped:
            msg += "\nSkipped Names:\n" + "\n".join(skipped)
        forms.alert(msg, title="Worksets Created")

    def show_unsupported(self, sender, args):
        forms.alert(
            "This action is not supported by the Revit API.", title="Not Allowed"
        )

    def close_form(self, sender, args):
        self.Close()


# Run
form = WorksetManagerForm()
form.ShowDialog()
