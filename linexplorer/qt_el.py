"""Greek for Qt's own strings (standard buttons, text-field menus, file dialog) - the PySide6
wheel ships no qtbase_el.qm."""
from __future__ import annotations

from PySide6.QtCore import QTranslator

EL = {
    "QPlatformTheme": {
        "OK": "OK", "Save": "Αποθήκευση", "Save All": "Αποθήκευση όλων", "Open": "Άνοιγμα",
        "&Yes": "&Ναι", "Yes to &All": "Ναι σε ό&λα", "&No": "Ό&χι", "N&o to All": "Όχ&ι σε όλα",
        "Abort": "Ματαίωση", "Retry": "Επανάληψη", "Ignore": "Παράβλεψη", "Close": "Κλείσιμο",
        "Cancel": "Άκυρο", "Discard": "Απόρριψη", "Help": "Βοήθεια", "Apply": "Εφαρμογή",
        "Reset": "Επαναφορά", "Restore Defaults": "Επαναφορά προεπιλογών", "Don't Save": "Χωρίς αποθήκευση",
        "Close without Saving": "Κλείσιμο χωρίς αποθήκευση",
    },
    "QLineEdit": {
        "&Undo": "Α&ναίρεση", "&Redo": "Ε&πανάληψη", "Cu&t": "Απο&κοπή", "&Copy": "Α&ντιγραφή",
        "&Paste": "Επι&κόλληση", "Delete": "Διαγραφή", "Select All": "Επιλογή όλων",
    },
    "QWidgetTextControl": {
        "&Undo": "Α&ναίρεση", "&Redo": "Ε&πανάληψη", "Cu&t": "Απο&κοπή", "&Copy": "Α&ντιγραφή",
        "&Paste": "Επι&κόλληση", "Delete": "Διαγραφή", "Select All": "Επιλογή όλων",
        "Copy &Link Location": "Αντιγραφή θέσης &σύνδεσης",
    },
    "QFileDialog": {
        "Look in:": "Αναζήτηση σε:", "File &name:": "Όνομα &αρχείου:", "Files of type:": "Αρχεία τύπου:",
        "&Open": "Ά&νοιγμα", "&Save": "&Αποθήκευση", "&Choose": "&Επιλογή", "Directory:": "Φάκελος:",
        "Back": "Πίσω", "Forward": "Εμπρός", "Parent Directory": "Γονικός φάκελος", "Create New Folder": "Νέος φάκελος",
        "List View": "Λίστα", "Detail View": "Λεπτομέρειες", "All Files (*)": "Όλα τα αρχεία (*)",
        "New Folder": "Νέος φάκελος", "Name": "Όνομα", "Size": "Μέγεθος", "Type": "Τύπος",
        "Date Modified": "Ημερομηνία τροποποίησης", "Find Directory": "Εύρεση φακέλου", "Open": "Άνοιγμα",
        "Save As": "Αποθήκευση ως", "Show &hidden files": "Εμφάνιση &κρυφών αρχείων", "&Delete": "&Διαγραφή",
        "&Rename": "&Μετονομασία", "&New Folder": "&Νέος φάκελος",
    },
    "QDialogButtonBox": {"OK": "OK", "Cancel": "Άκυρο", "&Yes": "&Ναι", "&No": "Ό&χι", "Close": "Κλείσιμο",
                         "Apply": "Εφαρμογή", "Help": "Βοήθεια"},
    "QMessageBox": {"Show Details...": "Εμφάνιση λεπτομερειών...", "Hide Details...": "Απόκρυψη λεπτομερειών..."},
    "QAbstractSpinBox": {"&Select All": "&Επιλογή όλων", "&Step up": "Αύ&ξηση", "Step &down": "Μεί&ωση"},
}


class GreekQt(QTranslator):
    def translate(self, context, source, disambiguation=None, n=-1):
        return EL.get(context, {}).get(source, "")

    def isEmpty(self):
        return False
