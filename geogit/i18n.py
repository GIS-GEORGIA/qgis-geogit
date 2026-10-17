"""Georgian / English strings for the GeoGit panel."""

LANG = "ka"

STRINGS = {
    "title": ("GeoGit – ვერსიები", "GeoGit – Versions"),
    "lang": ("ენა", "Language"),
    "folder": ("საქაღალდე", "Folder"),
    "no_folder": ("საქაღალდე არჩეული არ არის", "No folder selected"),
    "choose_folder": ("საქაღალდე...", "Folder..."),
    "project_folder": ("პროექტის საქაღალდე", "Project folder"),
    "not_repo": ("ეს საქაღალდე ჯერ ვერსიონირებული არ არის.", "This folder is not under version control yet."),
    "init": ("ვერსიონირების ჩართვა", "Start versioning"),
    "clone": ("სერვერიდან ჩამოტვირთვა...", "Get from server..."),
    "clone_url": ("რეპოს მისამართი (მაგ. https://github.com/org/repo.git):",
                  "Repository address (e.g. https://github.com/org/repo.git):"),
    "clone_dest": ("სად შევინახო?", "Where to save it?"),
    "branch": ("ბრენჩი", "Branch"),
    "new_branch": ("ახალი ბრენჩი", "New branch"),
    "new_branch_name": ("ახალი ბრენჩის სახელი:", "New branch name:"),
    "remote": ("სერვერი", "Server"),
    "no_remote": ("სერვერი მითითებული არ არის", "No server set"),
    "set_remote": ("სერვერის მითითება...", "Set server..."),
    "remote_url": ("სერვერის რეპოს მისამართი:", "Server repository address:"),
    "ahead_behind": ("↑{0} გასაგზავნი · ↓{1} მოსატანი", "↑{0} to publish · ↓{1} to get"),
    "changes": ("ცვლილებები", "Changes"),
    "history": ("ისტორია", "History"),
    "col_state": ("მდგომ.", "State"),
    "col_kind": ("ტიპი", "Type"),
    "col_path": ("ფაილი", "File"),
    "col_message": ("აღწერა", "Message"),
    "col_author": ("ავტორი", "Author"),
    "col_date": ("თარიღი", "Date"),
    "col_id": ("ID", "ID"),
    "select_all": ("ყველა", "All"),
    "message_ph": ("რა შეცვალე? (მაგ. დავამატე 2026 წლის ნაკვეთები)",
                   "What did you change? (e.g. added 2026 parcels)"),
    "commit": ("შენახვა ვერსიად", "Save as version"),
    "discard": ("ცვლილების გაუქმება", "Discard changes"),
    "discard_q": ("{0} ფაილის ცვლილება სამუდამოდ გაუქმდება. ახალი ფაილები წაიშლება. გავაგრძელო?",
                  "Changes to {0} file(s) will be lost; new files will be deleted. Continue?"),
    "pull": ("მოტანა", "Get updates"),
    "push": ("გამოქვეყნება", "Publish"),
    "refresh": ("განახლება", "Refresh"),
    "no_changes": ("ცვლილება არ არის", "No changes"),
    "n_changes": ("{0} ცვლილება", "{0} change(s)"),
    "need_message": ("დაწერე, რა შეცვალე.", "Write what you changed."),
    "need_selection": ("მონიშნე შესანახი ფაილები.", "Tick the files to save."),
    "committed": ("ვერსია შენახულია: {0}", "Version saved: {0}"),
    "pushed": ("გამოქვეყნდა სერვერზე.", "Published to the server."),
    "pulled": ("განახლებები მოტანილია.", "Updates received."),
    "cloned": ("ჩამოტვირთულია: {0}", "Downloaded: {0}"),
    "working": ("მიმდინარეობს: {0}...", "Working: {0}..."),
    "error": ("შეცდომა", "Error"),
    "save_project_q": ("პროექტში შეუნახავი ცვლილებებია. ჯერ პროექტი შევინახო?",
                       "The project has unsaved changes. Save the project first?"),
    "reload_project_q": ("პროექტის ფაილი სერვერიდან შეიცვალა. თავიდან გავხსნა?",
                         "The project file changed on the server. Reload it?"),
    "identity_name": ("შენი სახელი (ისტორიაში ასე გამოჩნდები):", "Your name (shown in history):"),
    "identity_email": ("შენი ელფოსტა:", "Your e-mail:"),
    "no_git": ("git ვერ ვიპოვე. დააყენე git და გადატვირთე QGIS.",
               "git was not found. Install git and restart QGIS."),
    "no_lfs": ("git-lfs დაყენებული არ არის: დიდი რასტრები და GPKG ჩვეულებრივ git-ში შეინახება. "
               "ჯობს დააყენო git-lfs და თავიდან ჩართო.",
               "git-lfs is not installed: large rasters and GPKG will be stored in plain git. "
               "Install git-lfs and start versioning again."),
    "auth_hint": ("\n\nთუ სერვერი GitHub-ია, ტერმინალში ერთხელ გაუშვი: gh auth login && gh auth setup-git",
                  "\n\nFor GitHub, run once in a terminal: gh auth login && gh auth setup-git"),
    "kind_vector": ("ვექტორი", "vector"),
    "kind_raster": ("რასტრი", "raster"),
    "kind_project": ("პროექტი", "project"),
    "kind_style": ("სტილი", "style"),
    "kind_other": ("სხვა", "other"),
    "state_A": ("ახალი", "new"),
    "state_M": ("შეცვლილი", "changed"),
    "state_D": ("წაშლილი", "deleted"),
    "state_R": ("გადარქმეული", "renamed"),
    "state_U": ("კონფლიქტი", "conflict"),
    "files_in_commit": ("ფაილები ამ ვერსიაში", "Files in this version"),
    "all_authors": ("ყველა ავტორი ({0})", "All authors ({0})"),
    "all_kinds": ("ყველა ტიპი", "All types"),
    "search_ph": ("ძებნა: აღწერა, ფაილი ან ID", "Search: message, file or ID"),
    "hide_others": ("დანარჩენის დამალვა", "Hide others"),
    "file_history": ("ფაილის ისტორია", "File history"),
    "show_file_history": ("ამ ფაილის ისტორია", "History of this file"),
    "clear_filter": ("ფილტრის მოხსნა", "Clear filter"),
    "show_on_map": ("ცვლილებები რუკაზე", "Show changes on map"),
    "working_copy": ("შეუნახავი", "unsaved"),
    "change_added": ("დამატებული", "added"),
    "change_modified": ("შეცვლილი", "modified"),
    "change_modified_old": ("შეცვლილი (ძველი სახე)", "modified (before)"),
    "change_deleted": ("წაშლილი", "deleted"),
    "diff_summary": ("{0}: +{1} დამატებული, ~{2} შეცვლილი, −{3} წაშლილი",
                     "{0}: +{1} added, ~{2} modified, −{3} deleted"),
    "diff_fields": (" · ახალი ველები: {0} · წაშლილი ველები: {1}", " · new fields: {0} · removed fields: {1}"),
    "no_vector_changes": ("{0}: ობიექტების დონეზე ცვლილება არ არის.", "{0}: no feature-level changes."),
    "comparing": ("შედარება", "Comparing"),
    "dblclick_hint": ("ორჯერ დააწკაპუნე ვექტორულ ან რასტრულ ფაილზე, რომ ცვლილებები რუკაზე ნახო",
                      "Double-click a vector or raster file to see its changes on the map"),
    "raster_old": ("ძველი", "before"),
    "raster_new": ("ახალი", "after"),
    "raster_diff": ("სხვაობა", "difference"),
    "raster_areas": ("შეცვლილი უბნები", "changed areas"),
    "raster_summary": ("{0}: შეიცვალა {1} პიქსელი ({2:.2f}%)", "{0}: {1} pixels changed ({2:.2f}%)"),
    "raster_range": (" · სხვაობა {0:+.4g} … {1:+.4g}", " · difference {0:+.4g} … {1:+.4g}"),
    "raster_gain_loss": (" · +{0} ახალი მონაცემი, −{1} დაკარგული", " · +{0} new data, −{1} lost"),
    "raster_meta": (" · შეიცვალა: {0}", " · changed: {0}"),
    "raster_not_compared": (" · პიქსელები ვერ შედარდა (სხვა არხები ან გეორეფერენსი არ აქვს)",
                            " · pixels not compared (different bands or no georeferencing)"),
    "raster_same": ("{0}: პიქსელები არ შეცვლილა.", "{0}: no pixel changes."),
    "raster_only_one": ("{0}: ფაილი მხოლოდ ერთ ვერსიაშია, ნაჩვენებია როგორც არის.",
                        "{0}: the file exists in one version only; shown as it is."),
    "swipe": ("Swipe შედარება", "Swipe compare"),
    "swipe_hint": ("მაუსის მარცხნივ ძველი ვერსიაა. გასასვლელად Esc ან მარჯვენა ღილაკი.",
                   "Left of the mouse is the old version. Esc or right-click to stop."),
    "meta_size": ("ზომა", "size"),
    "meta_bands": ("არხები", "bands"),
    "meta_dtype": ("მონაცემის ტიპი", "data type"),
    "meta_grid": ("ბადე/რეზოლუცია", "grid/resolution"),
    "meta_crs": ("CRS", "CRS"),
    "meta_nodata": ("nodata", "nodata"),
}


def set_lang(lang):
    global LANG
    LANG = "en" if lang == "en" else "ka"


def locale_lang():
    try:
        from qgis.core import QgsSettings
        loc = str(QgsSettings().value("locale/userLocale", "") or "")
    except ImportError:
        loc = ""
    return "ka" if loc.lower().startswith("ka") else "en"


def t(key, *args, **kw):
    ka, en = STRINGS[key]
    text = en if LANG == "en" else ka
    return text.format(*args, **kw) if (args or kw) else text
