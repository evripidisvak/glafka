use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk4::{gio, glib};
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use crate::book_card::create_book_card;
use crate::book_object::BookObject;
use crate::details_pane::DetailsPane;
use crate::preferences::show_preferences_dialog;
use grammata_core::{AppConfig, BookFilter, BookSort, LibraryManager};

glib::wrapper! {
    pub struct Window(ObjectSubclass<imp::Window>)
        @extends adw::ApplicationWindow, gtk4::ApplicationWindow, gtk4::Window, gtk4::Widget,
        @implements gio::ActionGroup, gio::ActionMap, gtk4::Accessible, gtk4::Buildable,
                    gtk4::ConstraintTarget, gtk4::Native, gtk4::Root, gtk4::ShortcutManager;
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct Window {
        pub library: RefCell<Option<Rc<RefCell<LibraryManager>>>>,
        pub list_store: RefCell<Option<gio::ListStore>>,
        pub toast_overlay: adw::ToastOverlay,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Window {
        const NAME: &'static str = "GrammataWindow";
        type Type = super::Window;
        type ParentType = adw::ApplicationWindow;
    }

    impl ObjectImpl for Window {
        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            obj.setup_ui();
        }
    }

    impl WidgetImpl for Window {}
    impl WindowImpl for Window {}
    impl ApplicationWindowImpl for Window {}
    impl AdwApplicationWindowImpl for Window {}
}

impl Window {
    pub fn new(app: &adw::Application) -> Self {
        glib::Object::builder()
            .property("application", app)
            .property("title", "Grammata")
            .property("default-width", 1120)
            .property("default-height", 760)
            .build()
    }

    fn setup_ui(&self) {
        // Initialize Library Manager from AppConfig
        let is_first_run = !AppConfig::exists();
        let config = AppConfig::load();
        if is_first_run {
            let _ = config.save();
        }

        let lib_manager = match LibraryManager::open(&config.library_path) {
            Ok(mgr) => Rc::new(RefCell::new(mgr)),
            Err(e) => {
                tracing::error!("Failed to open library: {}", e);
                return;
            }
        };
        self.imp().library.replace(Some(lib_manager.clone()));

        // Data model
        let list_store = gio::ListStore::new::<BookObject>();
        self.imp().list_store.replace(Some(list_store.clone()));

        // Root Toast Overlay
        let toast_overlay = &self.imp().toast_overlay;

        // Outer NavigationSplitView (Sidebar on left, Books & Details in Content)
        let root_split = adw::NavigationSplitView::builder()
            .min_sidebar_width(220.0)
            .max_sidebar_width(260.0)
            .build();

        // 1. Sidebar (Categories)
        let sidebar_view = adw::ToolbarView::new();
        let sidebar_header = adw::HeaderBar::builder()
            .show_end_title_buttons(false)
            .build();
        sidebar_view.add_top_bar(&sidebar_header);

        let sidebar_list = gtk4::ListBox::builder()
            .selection_mode(gtk4::SelectionMode::Single)
            .css_classes(["navigation-sidebar"])
            .build();

        let categories = [
            ("All Books", "emblem-documents-symbolic"),
            ("Recently Added", "document-open-recent-symbolic"),
            ("Authors", "avatar-default-symbolic"),
            ("Series", "media-playlist-consecutive-symbolic"),
            ("Tags", "folder-saved-search-symbolic"),
            ("Sent to Reader", "media-removable-symbolic"),
        ];

        for (title, icon) in categories {
            let row = adw::ActionRow::builder()
                .title(title)
                .activatable(true)
                .build();
            row.add_prefix(&gtk4::Image::from_icon_name(icon));
            sidebar_list.append(&row);
        }

        let sidebar_scroll = gtk4::ScrolledWindow::builder()
            .hscrollbar_policy(gtk4::PolicyType::Never)
            .child(&sidebar_list)
            .build();
        sidebar_view.set_content(Some(&sidebar_scroll));

        let sidebar_nav_page = adw::NavigationPage::builder()
            .title("Library")
            .child(&sidebar_view)
            .build();
        root_split.set_sidebar(Some(&sidebar_nav_page));

        // 2. Main Content Area with Details Overlay Split View
        let content_view = adw::ToolbarView::new();
        let content_header = adw::HeaderBar::new();

        // Search Entry
        let search_entry = gtk4::SearchEntry::builder()
            .placeholder_text("Search title, author, series...")
            .width_request(280)
            .build();
        content_header.set_title_widget(Some(&search_entry));

        // Add Button
        let add_btn = gtk4::Button::builder()
            .icon_name("list-add-symbolic")
            .tooltip_text("Add Books (EPUB, PDF, or Calibre Library)")
            .build();
        content_header.pack_start(&add_btn);

        // Main Menu (Preferences, About)
        let menu_model = gio::Menu::new();
        menu_model.append(Some("Preferences"), Some("win.preferences"));
        menu_model.append(Some("About Grammata"), Some("win.about"));

        let menu_btn = gtk4::MenuButton::builder()
            .icon_name("open-menu-symbolic")
            .menu_model(&menu_model)
            .primary(true)
            .tooltip_text("Main Menu")
            .build();
        content_header.pack_end(&menu_btn);

        content_view.add_top_bar(&content_header);

        // Filter and Sort Models
        let search_text = Rc::new(RefCell::new(String::new()));
        let active_category = Rc::new(RefCell::new("All Books".to_string()));

        let search_text_for_filter = search_text.clone();
        let active_category_for_filter = active_category.clone();

        let filter = gtk4::CustomFilter::new(move |obj| {
            let book_obj = obj.downcast_ref::<BookObject>().unwrap();
            let query = search_text_for_filter.borrow().to_lowercase();
            if !query.is_empty() {
                let matches_query = book_obj.title().to_lowercase().contains(&query)
                    || book_obj.author().to_lowercase().contains(&query)
                    || book_obj.series().map(|s| s.to_lowercase().contains(&query)).unwrap_or(false);
                if !matches_query {
                    return false;
                }
            }

            let cat = active_category_for_filter.borrow();
            match cat.as_str() {
                "Series" => book_obj.series().is_some(),
                "Tags" => !book_obj.tags().is_empty(),
                _ => true,
            }
        });

        let filter_model = gtk4::FilterListModel::new(Some(list_store.clone()), Some(filter.clone()));

        // Sorter: dynamic based on active category
        let active_category_for_sorter = active_category.clone();
        let sorter = gtk4::CustomSorter::new(move |a, b| {
            let book_a = a.downcast_ref::<BookObject>().unwrap();
            let book_b = b.downcast_ref::<BookObject>().unwrap();
            let cat = active_category_for_sorter.borrow();
            match cat.as_str() {
                "Recently Added" => book_b.created_at_timestamp().cmp(&book_a.created_at_timestamp()).into(),
                "Authors" => book_a.author().to_lowercase().cmp(&book_b.author().to_lowercase()).into(),
                "Series" => {
                    let s_a = book_a.series().unwrap_or_default().to_lowercase();
                    let s_b = book_b.series().unwrap_or_default().to_lowercase();
                    match s_a.cmp(&s_b) {
                        std::cmp::Ordering::Equal => {
                            let idx_a = book_a.series_index().unwrap_or(0);
                            let idx_b = book_b.series_index().unwrap_or(0);
                            idx_a.cmp(&idx_b).into()
                        }
                        other => other.into(),
                    }
                }
                _ => book_a.title().to_lowercase().cmp(&book_b.title().to_lowercase()).into(),
            }
        });
        let sort_model = gtk4::SortListModel::new(Some(filter_model.clone()), Some(sorter.clone()));

        // Selection Model
        let selection_model = gtk4::SingleSelection::new(Some(sort_model.clone()));

        // Details Pane setup
        let overlay_split = adw::OverlaySplitView::builder()
            .sidebar_position(gtk4::PackType::End)
            .min_sidebar_width(340.0)
            .max_sidebar_width(380.0)
            .show_sidebar(false)
            .build();

        let window_weak = self.downgrade();
        let list_store_for_save = list_store.clone();

        let overlay_split_for_close = overlay_split.clone();
        let details_pane = DetailsPane::new(
            move |id, title, author, series, series_idx, description, tags| {
                let win = match window_weak.upgrade() {
                    Some(w) => w,
                    None => return,
                };

                let lib_rc = match win.imp().library.borrow().clone() {
                    Some(l) => l,
                    None => return,
                };

                let mut mgr = lib_rc.borrow_mut();
                if let Err(e) = mgr.update_metadata(
                    &id,
                    &title,
                    &author,
                    series.as_deref(),
                    series_idx,
                    description.as_deref(),
                    &tags,
                ) {
                    tracing::error!("Failed to update metadata: {}", e);
                    let toast = adw::Toast::new(&format!("Failed to save: {}", e));
                    win.imp().toast_overlay.add_toast(toast);
                    return;
                }

                // Update in list store
                if let Ok(Some(updated_book)) = mgr.get_book_by_id(&id) {
                    for i in 0..list_store_for_save.n_items() {
                        if let Some(item) = list_store_for_save.item(i) {
                            let book_obj = item.downcast_ref::<BookObject>().unwrap();
                            if book_obj.id() == id {
                                book_obj.update(updated_book);
                                // Same object: no redraw, but filter/sort re-evaluate the item
                                list_store_for_save.splice(i, 1, &[book_obj.clone()]);
                                break;
                            }
                        }
                    }
                }

                let toast = adw::Toast::new(&format!("Saved changes for '{}'", title));
                win.imp().toast_overlay.add_toast(toast);
            },
            move || {
                overlay_split_for_close.set_show_sidebar(false);
            },
        );

        overlay_split.set_sidebar(Some(&details_pane.container));

        // Grid View & Factory
        let factory = gtk4::SignalListItemFactory::new();
        factory.connect_setup(|_, list_item| {
            let item = list_item.downcast_ref::<gtk4::ListItem>().unwrap();
            let holder = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
            item.set_child(Some(&holder));
        });

        // Tile -> (bound book, its "changed" handler); disconnected on unbind as tiles recycle
        let card_handlers: Rc<RefCell<HashMap<gtk4::ListItem, (BookObject, glib::SignalHandlerId)>>> =
            Rc::default();

        let handlers_for_bind = card_handlers.clone();
        factory.connect_bind(move |_, list_item| {
            let item = list_item.downcast_ref::<gtk4::ListItem>().unwrap();
            let book_obj = item
                .item()
                .and_downcast::<BookObject>()
                .expect("Item must be BookObject");
            let holder = item
                .child()
                .and_downcast::<gtk4::Box>()
                .expect("Child must be Box");

            fill_card(&holder, &book_obj);

            let holder_weak = holder.downgrade();
            let handler_id = book_obj.connect_local("changed", false, move |args| {
                let book = args[0].get::<BookObject>().expect("Signal emitter is BookObject");
                if let Some(holder) = holder_weak.upgrade() {
                    fill_card(&holder, &book);
                }
                None
            });
            handlers_for_bind
                .borrow_mut()
                .insert(item.clone(), (book_obj, handler_id));
        });

        let handlers_for_unbind = card_handlers;
        factory.connect_unbind(move |_, list_item| {
            let item = list_item.downcast_ref::<gtk4::ListItem>().unwrap();
            if let Some((book, handler_id)) = handlers_for_unbind.borrow_mut().remove(item) {
                book.disconnect(handler_id);
            }
        });

        let grid_view = gtk4::GridView::builder()
            .model(&selection_model)
            .factory(&factory)
            .min_columns(2)
            .max_columns(8)
            .margin_start(16)
            .margin_end(16)
            .margin_top(16)
            .margin_bottom(16)
            .build();

        let grid_scroll = gtk4::ScrolledWindow::builder()
            .hscrollbar_policy(gtk4::PolicyType::Never)
            .child(&grid_view)
            .vexpand(true)
            .build();

        // Stack for empty state vs grid
        let view_stack = gtk4::Stack::new();

        let empty_status_page = adw::StatusPage::builder()
            .icon_name("emblem-documents-symbolic")
            .title("No Books in Library")
            .description("Import EPUB, PDF, or an existing Calibre library to get started.")
            .build();

        let empty_add_btn = gtk4::Button::builder()
            .label("Add Books")
            .halign(gtk4::Align::Center)
            .css_classes(["suggested-action", "pill"])
            .build();
        empty_status_page.set_child(Some(&empty_add_btn));

        view_stack.add_named(&empty_status_page, Some("empty"));
        view_stack.add_named(&grid_scroll, Some("grid"));

        overlay_split.set_content(Some(&view_stack));
        content_view.set_content(Some(&overlay_split));

        let content_nav_page = adw::NavigationPage::builder()
            .title("Books")
            .child(&content_view)
            .build();
        root_split.set_content(Some(&content_nav_page));

        toast_overlay.set_child(Some(&root_split));
        self.set_content(Some(toast_overlay));

        // Connect Selection -> Show Details Pane
        let details_pane_clone = details_pane.clone();
        let overlay_split_clone = overlay_split.clone();
        selection_model.connect_selected_item_notify(move |sel| {
            if let Some(item) = sel.selected_item() {
                let book_obj = item.downcast_ref::<BookObject>().cloned();
                details_pane_clone.set_book(book_obj);
                overlay_split_clone.set_show_sidebar(true);
            }
        });

        // Search entry live filtering
        let filter_clone = filter.clone();
        search_entry.connect_search_changed(move |entry| {
            search_text.replace(entry.text().to_string());
            filter_clone.changed(gtk4::FilterChange::Different);
        });

        // Sidebar selection filtering & sorting
        let filter_for_sidebar = filter.clone();
        let sorter_for_sidebar = sorter.clone();
        let active_category_for_selection = active_category.clone();

        sidebar_list.connect_row_selected(move |_, row_opt| {
            if let Some(row) = row_opt {
                if let Some(action_row) = row.downcast_ref::<adw::ActionRow>() {
                    let title = action_row.title().to_string();
                    active_category_for_selection.replace(title);
                    filter_for_sidebar.changed(gtk4::FilterChange::Different);
                    sorter_for_sidebar.changed(gtk4::SorterChange::Different);
                }
            }
        });

        // Select first sidebar row ("All Books") by default
        if let Some(first_row) = sidebar_list.row_at_index(0) {
            sidebar_list.select_row(Some(&first_row));
        }

        // Load initial books from SQLite into ListStore
        self.reload_books();

        // Connect Add button
        let win_weak_add = self.downgrade();
        add_btn.connect_clicked(move |_| {
            if let Some(win) = win_weak_add.upgrade() {
                win.open_import_dialog();
            }
        });

        let win_weak_empty_add = self.downgrade();
        empty_add_btn.connect_clicked(move |_| {
            if let Some(win) = win_weak_empty_add.upgrade() {
                win.open_import_dialog();
            }
        });

        // Actions for Preferences and About
        let action_prefs = gio::SimpleAction::new("preferences", None);
        let win_weak_prefs = self.downgrade();
        action_prefs.connect_activate(move |_, _| {
            if let Some(win) = win_weak_prefs.upgrade() {
                win.open_preferences();
            }
        });
        self.add_action(&action_prefs);

        let action_about = gio::SimpleAction::new("about", None);
        let win_weak_about = self.downgrade();
        action_about.connect_activate(move |_, _| {
            if let Some(win) = win_weak_about.upgrade() {
                win.open_about();
            }
        });
        self.add_action(&action_about);

        // Update empty stack visibility
        let view_stack_clone = view_stack.clone();
        let update_visibility = move |count: u32| {
            if count == 0 {
                view_stack_clone.set_visible_child_name("empty");
            } else {
                view_stack_clone.set_visible_child_name("grid");
            }
        };

        update_visibility(filter_model.n_items());
        filter_model.connect_items_changed(move |model, _, _, _| {
            update_visibility(model.n_items());
        });

        // Prompt user on first launch to choose/confirm library location
        if is_first_run {
            let win_weak_first = self.downgrade();
            glib::idle_add_local_once(move || {
                if let Some(win) = win_weak_first.upgrade() {
                    win.open_preferences();
                }
            });
        }
    }

    pub fn reload_books(&self) {
        if let Some(ref lib_rc) = *self.imp().library.borrow() {
            if let Some(ref store) = *self.imp().list_store.borrow() {
                store.remove_all();
                let mgr = lib_rc.borrow();
                if let Ok(books) = mgr.get_books(&BookFilter::default(), BookSort::TitleAsc) {
                    for book in books {
                        store.append(&BookObject::new(book));
                    }
                }
            }
        }
    }

    fn open_import_dialog(&self) {
        let dialog = gtk4::FileDialog::builder()
            .title("Select Ebooks or Calibre Library")
            .build();

        // Create file filters
        let filter_all = gtk4::FileFilter::new();
        filter_all.set_name(Some("Ebook Files (*.epub, *.pdf, *.mobi)"));
        filter_all.add_pattern("*.epub");
        filter_all.add_pattern("*.pdf");
        filter_all.add_pattern("*.mobi");
        filter_all.add_pattern("*.azw3");

        let filters = gio::ListStore::new::<gtk4::FileFilter>();
        filters.append(&filter_all);
        dialog.set_filters(Some(&filters));

        let win_weak = self.downgrade();
        dialog.open_multiple(Some(self), gio::Cancellable::NONE, move |result| {
            let win = match win_weak.upgrade() {
                Some(w) => w,
                None => return,
            };

            if let Ok(files) = result {
                let mut imported_count = 0;
                for i in 0..files.n_items() {
                    if let Some(file_obj) = files.item(i) {
                        let file = file_obj.downcast_ref::<gio::File>().unwrap();
                        if let Some(path) = file.path() {
                            if let Some(ref lib_rc) = *win.imp().library.borrow() {
                                let mut mgr = lib_rc.borrow_mut();
                                if path.is_file() {
                                    if let Ok(Some(book)) = mgr.import_file(&path) {
                                        if let Some(ref store) = *win.imp().list_store.borrow() {
                                            store.append(&BookObject::new(book));
                                        }
                                        imported_count += 1;
                                    }
                                }
                            }
                        }
                    }
                }

                if imported_count > 0 {
                    let msg = format!("Imported {} book(s) into library", imported_count);
                    let toast = adw::Toast::new(&msg);
                    win.imp().toast_overlay.add_toast(toast);
                }
            }
        });
    }

    pub fn open_preferences(&self) {
        let win_weak = self.downgrade();
        show_preferences_dialog(self, move |new_path| {
            if let Some(win) = win_weak.upgrade() {
                match LibraryManager::open(&new_path) {
                    Ok(mgr) => {
                        win.imp().library.replace(Some(Rc::new(RefCell::new(mgr))));
                        win.reload_books();
                        let toast = adw::Toast::new(&format!("Library switched to {}", new_path.display()));
                        win.imp().toast_overlay.add_toast(toast);
                    }
                    Err(e) => {
                        let toast = adw::Toast::new(&format!("Failed to open library: {}", e));
                        win.imp().toast_overlay.add_toast(toast);
                    }
                }
            }
        });
    }

    pub fn open_about(&self) {
        let about = adw::AboutDialog::builder()
            .application_name("Grammata")
            .developer_name("Evripidis Vakalis")
            .version("0.1.0")
            .comments("A lightweight, GNOME-native ebook library manager")
            .website("https://github.com/evripidisvak/Grammata")
            .license_type(gtk4::License::Gpl30)
            .build();
        about.present(Some(self));
    }
}

fn fill_card(holder: &gtk4::Box, book: &BookObject) {
    while let Some(child) = holder.first_child() {
        holder.remove(&child);
    }
    holder.append(&create_book_card(book));
}
