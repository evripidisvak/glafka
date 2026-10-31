use adw::prelude::*;
use gtk4::gio;
use std::path::PathBuf;
use std::rc::Rc;

use grammata_core::AppConfig;

pub fn show_preferences_dialog<W, F>(
    parent: &W,
    on_library_changed: F,
) where
    W: IsA<gtk4::Window> + IsA<gtk4::Widget> + Clone + 'static,
    F: Fn(PathBuf) + 'static,
{
    let dialog = adw::PreferencesDialog::builder()
        .title("Preferences")
        .build();

    let page = adw::PreferencesPage::builder()
        .title("General")
        .icon_name("preferences-system-symbolic")
        .build();

    let group = adw::PreferencesGroup::builder()
        .title("Library Storage")
        .description("Choose where Grammata organizes your book collection and SQLite index.")
        .build();

    let config = AppConfig::load();
    let current_path = config.library_path;

    let path_row = adw::ActionRow::builder()
        .title("Library Folder")
        .subtitle(&*current_path.to_string_lossy())
        .build();

    let change_btn = gtk4::Button::builder()
        .label("Change...")
        .valign(gtk4::Align::Center)
        .build();

    let path_row_clone = path_row.clone();
    let parent_clone = parent.clone();
    let on_change_rc = Rc::new(on_library_changed);

    change_btn.connect_clicked(move |_| {
        let file_dialog = gtk4::FileDialog::builder()
            .title("Select Library Folder")
            .build();

        let path_row_inner = path_row_clone.clone();
        let on_change_inner = on_change_rc.clone();

        file_dialog.select_folder(Some(&parent_clone), gio::Cancellable::NONE, move |res| {
            if let Ok(folder) = res {
                if let Some(path) = folder.path() {
                    let mut cfg = AppConfig::load();
                    cfg.library_path = path.clone();
                    let _ = cfg.save();

                    path_row_inner.set_subtitle(&*path.to_string_lossy());
                    on_change_inner(path);
                }
            }
        });
    });

    path_row.add_suffix(&change_btn);
    group.add(&path_row);
    page.add(&group);
    dialog.add(&page);

    dialog.present(Some(parent));
}
