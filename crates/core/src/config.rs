use std::fs;
use std::path::PathBuf;
use serde::{Deserialize, Serialize};

use crate::error::Result;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub library_path: PathBuf,
}

impl Default for AppConfig {
    fn default() -> Self {
        let path = if let Some(home) = std::env::var_os("HOME") {
            PathBuf::from(home).join("Books").join("Grammata")
        } else {
            PathBuf::from("Grammata")
        };
        Self { library_path: path }
    }
}

impl AppConfig {
    pub fn config_file_path() -> PathBuf {
        if let Some(config_dir) = std::env::var_os("XDG_CONFIG_HOME") {
            PathBuf::from(config_dir).join("grammata").join("config.json")
        } else if let Some(home) = std::env::var_os("HOME") {
            PathBuf::from(home).join(".config").join("grammata").join("config.json")
        } else {
            PathBuf::from("config.json")
        }
    }

    pub fn exists() -> bool {
        Self::config_file_path().exists()
    }

    pub fn load() -> Self {
        let path = Self::config_file_path();
        if path.exists() {
            if let Ok(content) = fs::read_to_string(&path) {
                if let Ok(cfg) = serde_json::from_str(&content) {
                    return cfg;
                }
            }
        }
        Self::default()
    }

    pub fn save(&self) -> Result<()> {
        let path = Self::config_file_path();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let json = serde_json::to_string_pretty(self)
            .map_err(|e| crate::error::Error::Other(e.to_string()))?;
        fs::write(&path, json)?;
        Ok(())
    }
}
