use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk4::glib;

use crate::window::Window;

pub const APP_ID: &str = "io.github.evripidisvak.Grammata";

glib::wrapper! {
    pub struct App(ObjectSubclass<imp::App>)
        @extends adw::Application, gtk4::Application, gio::Application,
        @implements gio::ActionGroup, gio::ActionMap;
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct App {}

    #[glib::object_subclass]
    impl ObjectSubclass for App {
        const NAME: &'static str = "GrammataApp";
        type Type = super::App;
        type ParentType = adw::Application;
    }

    impl ObjectImpl for App {}

    impl ApplicationImpl for App {
        fn activate(&self) {
            let app = self.obj();
            let window = Window::new(app.upcast_ref::<adw::Application>());
            window.present();
        }
    }

    impl GtkApplicationImpl for App {}
    impl AdwApplicationImpl for App {}
}

impl App {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("application-id", APP_ID)
            .property("flags", gio::ApplicationFlags::FLAGS_NONE)
            .build()
    }
}
