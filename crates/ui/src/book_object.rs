use glib::prelude::*;
use glib::subclass::prelude::*;
use gtk4::glib;
use std::cell::RefCell;

use grammata_core::Book;

glib::wrapper! {
    pub struct BookObject(ObjectSubclass<imp::BookObject>);
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct BookObject {
        pub data: RefCell<Option<Book>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for BookObject {
        const NAME: &'static str = "GrammataBookObject";
        type Type = super::BookObject;
        type ParentType = glib::Object;
    }

    impl ObjectImpl for BookObject {
        fn signals() -> &'static [glib::subclass::Signal] {
            static SIGNALS: std::sync::OnceLock<Vec<glib::subclass::Signal>> = std::sync::OnceLock::new();
            SIGNALS.get_or_init(|| vec![glib::subclass::Signal::builder("changed").build()])
        }
    }
}

impl BookObject {
    pub fn new(book: Book) -> Self {
        let obj: Self = glib::Object::builder().build();
        obj.imp().data.replace(Some(book));
        obj
    }

    pub fn book(&self) -> Book {
        self.imp().data.borrow().as_ref().cloned().expect("Book data must be set")
    }

    pub fn id(&self) -> String {
        self.imp().data.borrow().as_ref().map(|b| b.id.clone()).unwrap_or_default()
    }

    pub fn title(&self) -> String {
        self.imp().data.borrow().as_ref().map(|b| b.title.clone()).unwrap_or_default()
    }

    pub fn author(&self) -> String {
        self.imp().data.borrow().as_ref().map(|b| b.author.clone()).unwrap_or_default()
    }

    pub fn series(&self) -> Option<String> {
        self.imp().data.borrow().as_ref().and_then(|b| b.series.clone())
    }

    pub fn series_index(&self) -> Option<i32> {
        self.imp().data.borrow().as_ref().and_then(|b| b.series_index)
    }

    pub fn tags(&self) -> Vec<String> {
        self.imp().data.borrow().as_ref().map(|b| b.tags.clone()).unwrap_or_default()
    }

    pub fn created_at_timestamp(&self) -> i64 {
        self.imp().data.borrow().as_ref().map(|b| b.created_at.timestamp()).unwrap_or(0)
    }

    pub fn cover_path(&self) -> Option<String> {
        self.imp().data.borrow().as_ref().and_then(|b| b.cover_path.clone())
    }

    pub fn formats(&self) -> Vec<String> {
        self.imp().data.borrow().as_ref().map(|b| {
            b.files.iter().map(|f| f.format.to_uppercase()).collect()
        }).unwrap_or_default()
    }

    /// GTK rebinds tiles only when the item pointer changes; in-place edits need "changed".
    pub fn update(&self, new_book: Book) {
        self.imp().data.replace(Some(new_book));
        self.emit_by_name::<()>("changed", &[]);
    }
}
