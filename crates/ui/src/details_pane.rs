use adw::prelude::*;
use std::cell::RefCell;
use std::path::Path;
use std::rc::Rc;

use crate::book_object::BookObject;

pub struct DetailsPane {
    pub container: gtk4::Box,
    current_book: RefCell<Option<BookObject>>,
    cover_picture: gtk4::Picture,
    title_entry: adw::EntryRow,
    author_entry: adw::EntryRow,
    series_entry: adw::EntryRow,
    series_index_spin: gtk4::SpinButton,
    description_view: gtk4::TextView,
    tags_entry: adw::EntryRow,
    formats_list: gtk4::ListBox,
}

impl DetailsPane {
    pub fn new<F, C>(on_save: F, on_close: C) -> Rc<Self>
    where
        F: Fn(String, String, String, Option<String>, Option<i32>, Option<String>, Vec<String>) + 'static,
        C: Fn() + 'static,
    {
        let container = gtk4::Box::builder()
            .orientation(gtk4::Orientation::Vertical)
            .width_request(340)
            .css_classes(["view", "background"])
            .build();

        // 1. Header with title, Save button on start, and Close button on end
        let header_bar = adw::HeaderBar::builder()
            .show_title(true)
            .show_start_title_buttons(false)
            .show_end_title_buttons(false)
            .title_widget(&adw::WindowTitle::new("Book Details", ""))
            .build();

        let save_btn = gtk4::Button::builder()
            .label("Save")
            .css_classes(["suggested-action"])
            .build();
        header_bar.pack_start(&save_btn);

        let close_btn = gtk4::Button::builder()
            .icon_name("window-close-symbolic")
            .tooltip_text("Close Details")
            .build();
        close_btn.connect_clicked(move |_| on_close());
        header_bar.pack_end(&close_btn);

        container.append(&header_bar);

        // 2. Scrolled content
        let scroll = gtk4::ScrolledWindow::builder()
            .hscrollbar_policy(gtk4::PolicyType::Never)
            .vexpand(true)
            .build();

        let content_box = gtk4::Box::builder()
            .orientation(gtk4::Orientation::Vertical)
            .spacing(16)
            .margin_start(16)
            .margin_end(16)
            .margin_top(16)
            .margin_bottom(24)
            .build();

        // Cover picture
        let cover_picture = gtk4::Picture::builder()
            .height_request(240)
            .can_shrink(true)
            .content_fit(gtk4::ContentFit::ScaleDown)
            .halign(gtk4::Align::Center)
            .css_classes(["card"])
            .build();
        content_box.append(&cover_picture);

        // Metadata Preferences Group
        let meta_group = adw::PreferencesGroup::builder()
            .title("Metadata")
            .build();

        let title_entry = adw::EntryRow::builder()
            .title("Title")
            .build();
        meta_group.add(&title_entry);

        let author_entry = adw::EntryRow::builder()
            .title("Author")
            .build();
        meta_group.add(&author_entry);

        let series_entry = adw::EntryRow::builder()
            .title("Series")
            .build();
        meta_group.add(&series_entry);

        let series_index_adj = gtk4::Adjustment::new(1.0, 0.0, 999.0, 1.0, 5.0, 0.0);
        let series_index_spin = gtk4::SpinButton::builder()
            .adjustment(&series_index_adj)
            .climb_rate(1.0)
            .digits(0)
            .valign(gtk4::Align::Center)
            .build();

        let series_index_row = adw::ActionRow::builder()
            .title("Series Index")
            .subtitle("0 for none")
            .build();
        series_index_row.add_suffix(&series_index_spin);
        meta_group.add(&series_index_row);

        let tags_entry = adw::EntryRow::builder()
            .title("Tags (comma separated)")
            .build();
        meta_group.add(&tags_entry);

        content_box.append(&meta_group);

        // Description Group
        let desc_group = adw::PreferencesGroup::builder()
            .title("Description")
            .build();

        let desc_buffer = gtk4::TextBuffer::new(None);
        let description_view = gtk4::TextView::builder()
            .buffer(&desc_buffer)
            .wrap_mode(gtk4::WrapMode::Word)
            .height_request(100)
            .top_margin(8)
            .bottom_margin(8)
            .left_margin(8)
            .right_margin(8)
            .css_classes(["card"])
            .build();
        desc_group.add(&description_view);
        content_box.append(&desc_group);

        // Formats Group
        let formats_group = adw::PreferencesGroup::builder()
            .title("Files &amp; Formats")
            .build();

        let formats_list = gtk4::ListBox::builder()
            .selection_mode(gtk4::SelectionMode::None)
            .css_classes(["boxed-list"])
            .build();
        formats_group.add(&formats_list);
        content_box.append(&formats_group);

        scroll.set_child(Some(&content_box));
        container.append(&scroll);

        let pane = Rc::new(Self {
            container,
            current_book: RefCell::new(None),
            cover_picture,
            title_entry,
            author_entry,
            series_entry,
            series_index_spin,
            description_view,
            tags_entry,
            formats_list,
        });

        // Wire save button
        let pane_clone = pane.clone();
        save_btn.connect_clicked(move |_| {
            if let Some(ref book_obj) = *pane_clone.current_book.borrow() {
                let id = book_obj.id();
                let title = pane_clone.title_entry.text().to_string();
                let author = pane_clone.author_entry.text().to_string();
                let series_raw = pane_clone.series_entry.text().to_string();
                let series = if series_raw.trim().is_empty() { None } else { Some(series_raw.trim().to_string()) };
                // 0 = no index; saving it as Some(0) would collide across index-less books
                let spin_idx = pane_clone.series_index_spin.value() as i32;
                let series_idx = if series.is_some() && spin_idx > 0 {
                    Some(spin_idx)
                } else {
                    None
                };

                let buffer = pane_clone.description_view.buffer();
                let desc_text = buffer.text(&buffer.start_iter(), &buffer.end_iter(), false).to_string();
                let description = if desc_text.trim().is_empty() { None } else { Some(desc_text) };

                let tags_str = pane_clone.tags_entry.text().to_string();
                let tags: Vec<String> = tags_str
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();

                on_save(id, title, author, series, series_idx, description, tags);
            }
        });

        pane
    }

    pub fn set_book(&self, book_obj: Option<BookObject>) {
        self.current_book.replace(book_obj.clone());

        if let Some(ref item) = book_obj {
            let book = item.book();

            // Cover
            if let Some(ref path) = book.cover_path {
                if Path::new(path).exists() {
                    let file = gio::File::for_path(path);
                    if let Ok(texture) = gtk4::gdk::Texture::from_file(&file) {
                        self.cover_picture.set_paintable(Some(&texture));
                    } else {
                        self.cover_picture.set_paintable(gtk4::gdk::Paintable::NONE);
                    }
                } else {
                    self.cover_picture.set_paintable(gtk4::gdk::Paintable::NONE);
                }
            } else {
                self.cover_picture.set_paintable(gtk4::gdk::Paintable::NONE);
            }

            self.title_entry.set_text(&book.title);
            self.author_entry.set_text(&book.author);
            self.series_entry.set_text(book.series.as_deref().unwrap_or(""));
            self.series_index_spin.set_value(book.series_index.unwrap_or(0) as f64);
            self.tags_entry.set_text(&book.tags.join(", "));

            let buffer = self.description_view.buffer();
            buffer.set_text(book.description.as_deref().unwrap_or(""));

            // Clear format rows
            while let Some(child) = self.formats_list.first_child() {
                self.formats_list.remove(&child);
            }

            // Populate formats
            for file in &book.files {
                let row = adw::ActionRow::builder()
                    .title(&file.format.to_uppercase())
                    .subtitle(&format!("{:.2} MB", file.file_size as f64 / (1024.0 * 1024.0)))
                    .build();

                let icon = gtk4::Image::from_icon_name("document-open-symbolic");
                row.add_prefix(&icon);
                self.formats_list.append(&row);
            }
        }
    }
}
