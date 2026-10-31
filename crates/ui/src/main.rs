mod app;
mod book_card;
mod book_object;
mod details_pane;
mod preferences;
mod window;

use adw::prelude::*;
use app::App;

fn main() -> glib::ExitCode {
    // Initialize logging
    tracing_subscriber::fmt::init();

    // Create and run the Libadwaita application
    let app = App::new();
    app.run()
}
