use gtk4::prelude::*;
use std::path::Path;

use crate::book_object::BookObject;

pub fn create_book_card(item: &BookObject) -> gtk4::Box {
    let container = gtk4::Box::builder()
        .orientation(gtk4::Orientation::Vertical)
        .spacing(6)
        .width_request(140)
        .margin_start(8)
        .margin_end(8)
        .margin_top(8)
        .margin_bottom(8)
        .css_classes(["card", "activatable"])
        .cursor(&gtk4::gdk::Cursor::from_name("pointer", None).unwrap())
        .build();

    // 1. Cover container
    let cover_box = gtk4::Box::builder()
        .orientation(gtk4::Orientation::Vertical)
        .height_request(200)
        .width_request(140)
        .valign(gtk4::Align::Center)
        .halign(gtk4::Align::Center)
        .build();

    let picture = gtk4::Picture::builder()
        .can_shrink(true)
        .content_fit(gtk4::ContentFit::Cover)
        .width_request(140)
        .height_request(200)
        .build();

    let mut loaded = false;
    if let Some(cover_path) = item.cover_path() {
        if Path::new(&cover_path).exists() {
            let file = gio::File::for_path(&cover_path);
            if let Ok(texture) = gtk4::gdk::Texture::from_file(&file) {
                picture.set_paintable(Some(&texture));
                loaded = true;
            }
        }
    }

    if loaded {
        cover_box.append(&picture);
    } else {
        // Fallback placeholder
        let placeholder = gtk4::Box::builder()
            .orientation(gtk4::Orientation::Vertical)
            .valign(gtk4::Align::Center)
            .halign(gtk4::Align::Center)
            .height_request(200)
            .width_request(140)
            .spacing(12)
            .css_classes(["view", "dim-label"])
            .build();

        let icon = gtk4::Image::builder()
            .icon_name("emblem-documents-symbolic")
            .pixel_size(48)
            .build();
        placeholder.append(&icon);
        cover_box.append(&placeholder);
    }

    container.append(&cover_box);

    // 2. Metadata Labels
    let text_box = gtk4::Box::builder()
        .orientation(gtk4::Orientation::Vertical)
        .spacing(2)
        .margin_start(8)
        .margin_end(8)
        .margin_bottom(8)
        .build();

    let title_label = gtk4::Label::builder()
        .label(&item.title())
        .halign(gtk4::Align::Start)
        .ellipsize(gtk4::pango::EllipsizeMode::End)
        .lines(1)
        .css_classes(["heading"])
        .build();
    text_box.append(&title_label);

    let author_label = gtk4::Label::builder()
        .label(&item.author())
        .halign(gtk4::Align::Start)
        .ellipsize(gtk4::pango::EllipsizeMode::End)
        .lines(1)
        .css_classes(["caption", "dim-label"])
        .build();
    text_box.append(&author_label);

    // Series badge if book is part of a series
    if let Some(series) = item.series() {
        let idx_str = if let Some(idx) = item.series_index() {
            format!(" #{}", idx)
        } else {
            String::new()
        };
        let series_label = gtk4::Label::builder()
            .label(&format!("{}{}", series, idx_str))
            .halign(gtk4::Align::Start)
            .ellipsize(gtk4::pango::EllipsizeMode::End)
            .lines(1)
            .css_classes(["caption", "accent"])
            .build();
        text_box.append(&series_label);
    }

    // Formats badges
    let formats_box = gtk4::Box::builder()
        .orientation(gtk4::Orientation::Horizontal)
        .spacing(4)
        .margin_top(4)
        .build();

    for fmt in item.formats() {
        let badge = gtk4::Label::builder()
            .label(&fmt)
            .css_classes(["caption", "tag", "numeric"])
            .build();
        formats_box.append(&badge);
    }
    text_box.append(&formats_box);

    container.append(&text_box);

    container
}
